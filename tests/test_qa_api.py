from __future__ import annotations

import asyncio
import concurrent.futures
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

import pytest

from worker import qa_api
from worker.langgraph_qa import interface
from worker.topic_policy import final_response_policy_text, CANONICAL_TERMINOLOGY_PROMPT
from worker.qa_response import AI_NOTICE_RESPONSES, GENERIC_ERROR_RESPONSES
from worker.conversation_store import ConversationTurn
from worker.prompt_security import GuardDecision


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_wiki_reader_only_loads_indexed_pages_and_duplicate_slugs(tmp_path: Path) -> None:
    write(tmp_path / "index.md", "[[allowed]] [[same|Same page]]")
    write(tmp_path / "concepts" / "allowed.md", "allowed")
    write(tmp_path / "concepts" / "hidden.md", "hidden")
    write(tmp_path / "a" / "same.md", "a")
    write(tmp_path / "b" / "same.md", "b")

    wiki = qa_api.Wiki(tmp_path)

    assert wiki.retrievable_slugs == {"allowed", "same"}
    assert [path.parent.name for path in wiki.pages["same"]] == ["a", "b"]
    assert "hidden" not in wiki.retrievable_slugs


def test_wiki_reader_resolves_directory_prefixed_index_links(tmp_path: Path) -> None:
    write(tmp_path / "index.md", "[[entities/tk-outdoornavigation]] [[concepts/joint-zeroing]]")
    write(tmp_path / "entities" / "tk-outdoornavigation.md", "Navigation evidence")
    write(tmp_path / "concepts" / "joint-zeroing.md", "Zeroing evidence")
    write(tmp_path / "entities" / "unlisted.md", "Not in index")

    wiki = qa_api.Wiki(tmp_path)

    assert wiki.retrievable_slugs == {"entities/tk-outdoornavigation", "concepts/joint-zeroing"}
    assert "unlisted" not in wiki.retrievable_slugs


def test_wiki_reader_rejects_symlink_root_and_skips_symlink_pages(tmp_path: Path) -> None:
    real_wiki = tmp_path / "wiki"
    outside = tmp_path / "outside.md"
    write(real_wiki / "index.md", "[[outside]]")
    write(outside, "original source must remain unavailable")
    (real_wiki / "outside.md").symlink_to(outside)

    wiki = qa_api.Wiki(real_wiki)
    assert wiki.retrievable_slugs == set()

    linked_root = tmp_path / "linked-wiki"
    linked_root.symlink_to(real_wiki, target_is_directory=True)
    with pytest.raises(ValueError, match="Wiki root cannot be a symlink"):
        qa_api.Wiki(linked_root)


def test_wiki_catalog_preserves_original_files(tmp_path: Path) -> None:
    index = tmp_path / "index.md"
    page = tmp_path / "entities" / "tiangong.md"
    write(index, "[[tiangong]] 天工2.0 Pro")
    write(page, "# 天工2.0 Pro\n产品介绍")
    original_index = index.read_bytes()
    original_page = page.read_bytes()

    wiki = qa_api.Wiki(tmp_path)
    assert wiki.retrievable_slugs == {"tiangong"}
    assert wiki.pages["tiangong"] == [page]
    assert index.read_bytes() == original_index
    assert page.read_bytes() == original_page


@dataclass
class FakeTeamConfig:
    wiki_dir: Path


class FakeDeepSeekClient:
    instances: ClassVar[list[FakeDeepSeekClient]] = []
    router_response: ClassVar[str] = '{"pages":["walker"]}'
    stream_chunks: ClassVar[tuple[str, ...]] = ("平台名称是慧思", "开物平台。")
    timeout = 2

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.__class__.instances.append(self)

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        if "Intent Planner" in system:
            return (
                '{"scope_analysis":{"active_scope":"scope","explicit_entities":[],'
                '"resolved_references":[],"relation":"in_scope","reason":"test",'
                '"confidence":0.9},"topic_relation":"continue","current_subject":null,'
                '"history_used":[],"history_ignored":[],"standalone_question":'
                '"Walker platform","intent":"concept","preferred_abstraction":'
                '"application_or_workflow","search_queries":["Walker"]}'
            )
        selected = "concepts/walker.md" if "concepts/walker.md" in user else "walker.md"
        return (
            '{"scope_consistency":{"valid":true,'
            '"unsupported_cross_scope_transfer":[]},"planner_faithful":true,'
            '"unsupported_assumptions":[],"corrected_standalone_question":null,'
            '"primary_solution":"Walker","selected_pages":["'
            + selected
            + '"],"selected_images":[],"need_more_search":false,'
            '"additional_search_queries":[],"uncertainties_to_check":[],'
            '"direct_answer_plan":"Answer from the Wiki.","supporting_points":[]}'
        )

    def stream(self, system: str, user: str):
        self.calls.append((system, user))
        yield from self.stream_chunks


@pytest.mark.anyio
async def test_stream_bridge_works_with_one_default_executor_thread() -> None:
    loop = asyncio.get_running_loop()
    loop.set_default_executor(concurrent.futures.ThreadPoolExecutor(max_workers=1))
    chunks: list[str] = []

    async def on_token(token: str) -> None:
        chunks.append(token)

    answer = await interface._drain_stream(
        iter(("one", "two")), on_token, timeout=2
    )

    assert answer == "onetwo"
    assert chunks == ["one", "two"]


def test_worker_stream_timeouts_remain_compatible_with_python_310() -> None:
    root = Path(__file__).resolve().parents[1]
    stream_sources = [
        (root / "worker" / "langgraph_qa" / "interface.py").read_text(
            encoding="utf-8"
        ),
    ]

    assert all("asyncio.timeout(" not in source for source in stream_sources)
    assert all("asyncio.wait_for(drain(), timeout=" in source for source in stream_sources)


@pytest.mark.anyio
async def test_deepseek_retrieval_preserves_language_team_history_and_response_boundary(
    monkeypatch, tmp_path: Path
) -> None:
    write(tmp_path / "index.md", "[[walker]] [[unrelated]]")
    write(tmp_path / "concepts" / "walker.md", "Walker evidence")
    write(tmp_path / "concepts" / "unrelated.md", "Other evidence")
    FakeDeepSeekClient.instances.clear()
    monkeypatch.setattr(qa_api, "DeepSeekClient", FakeDeepSeekClient)
    monkeypatch.setattr(qa_api, "get_team_config", lambda _team: FakeTeamConfig(tmp_path))
    chunks: list[str] = []

    async def on_chunk(text: str, _thinking: str, _tokens: int) -> None:
        chunks.append(text)

    answer = await qa_api.run_qa_api_stream(
        "这个平台叫什么？",
        team="walker_s2",
        language="zh-CN",
        history=[ConversationTurn(question="它是什么？", answer="一个机器人平台。")],
        on_chunk=on_chunk,
        guard_decision=GuardDecision(False, "none", "zh-CN"),
    )

    client = FakeDeepSeekClient.instances[0]
    assert final_response_policy_text() in client.calls[2][0]
    assert CANONICAL_TERMINOLOGY_PROMPT in client.calls[2][0]
    planner_prompt = client.calls[0][1]
    reasoner_prompt = client.calls[1][1]
    answer_prompt = client.calls[2][1]
    assert "Selected Robot Scope" in planner_prompt
    assert "Walker_S2_EDU探索者" in client.calls[0][0]
    assert "它是什么？" in planner_prompt
    assert "Current User Question" in planner_prompt
    assert "Candidate Wiki Pages" in reasoner_prompt
    assert "target language 'zh-CN'" in answer_prompt
    assert "Walker evidence" in answer_prompt
    assert answer == (
        "平台名称是Thinkerstudio遥操数采平台。\n\n"
        + AI_NOTICE_RESPONSES["zh-CN"]
    )
    assert "".join(chunks) == answer
    assert "慧思开物" not in "".join(chunks)


@pytest.mark.anyio
async def test_deepseek_failure_becomes_localized_user_safe_response(monkeypatch) -> None:
    async def fail(*_args, **_kwargs):
        raise qa_api.QAAPIError("provider key was rejected")

    monkeypatch.setattr(qa_api, "_retrieve_and_stream", fail)
    chunks: list[str] = []

    async def on_chunk(text: str, _thinking: str, _tokens: int) -> None:
        chunks.append(text)

    answer = await qa_api.run_qa_api_stream(
        "How do I start the robot?",
        team="tian_gong",
        language="en",
        on_chunk=on_chunk,
        guard_decision=GuardDecision(False, "none", "en"),
    )

    assert answer == GENERIC_ERROR_RESPONSES["en"] + "\n\n" + AI_NOTICE_RESPONSES["en"]
    assert "provider key" not in answer
    assert chunks == [answer]


@pytest.mark.anyio
async def test_real_deepseek_client_failure_is_bounded_and_hidden(
    monkeypatch, tmp_path: Path
) -> None:
    write(tmp_path / "index.md", "[[walker]]")
    write(tmp_path / "walker.md", "Walker evidence")

    class FailingDeepSeekClient(FakeDeepSeekClient):
        def complete(self, system: str, user: str) -> str:
            self.calls.append((system, user))
            raise RuntimeError("secret provider failure detail")

        def stream(self, system: str, user: str):
            self.calls.append((system, user))
            raise RuntimeError("secret provider failure detail")
            yield ""  # pragma: no cover

    FailingDeepSeekClient.instances.clear()
    monkeypatch.setattr(qa_api, "DeepSeekClient", FailingDeepSeekClient)
    monkeypatch.setattr(qa_api, "get_team_config", lambda _team: FakeTeamConfig(tmp_path))
    chunks: list[str] = []

    async def on_chunk(text: str, _thinking: str, _tokens: int) -> None:
        chunks.append(text)

    answer = await qa_api.run_qa_api_stream(
        "How do I start the robot?",
        team="walker_s2",
        language="en",
        on_chunk=on_chunk,
        guard_decision=GuardDecision(False, "none", "en"),
    )

    assert len(FailingDeepSeekClient.instances) == 1
    assert len(FailingDeepSeekClient.instances[0].calls) == 3
    assert "secret provider failure detail" not in answer
    assert answer == GENERIC_ERROR_RESPONSES["en"] + "\n\n" + AI_NOTICE_RESPONSES["en"]
    assert chunks == [answer]


@pytest.mark.anyio
async def test_predefined_response_does_not_call_deepseek(monkeypatch) -> None:
    async def forbidden(*_args, **_kwargs):
        raise AssertionError("predefined response must not call DeepSeek")

    monkeypatch.setattr(qa_api, "_retrieve_and_stream", forbidden)
    chunks: list[str] = []

    async def on_chunk(text: str, _thinking: str, _tokens: int) -> None:
        chunks.append(text)

    answer = await qa_api.run_qa_api_stream(
        "Separator is not found",
        team="all",
        language="en",
        on_chunk=on_chunk,
        guard_decision=GuardDecision(False, "none", "en"),
    )

    assert answer.endswith(AI_NOTICE_RESPONSES["en"])
    assert chunks == [answer]


def test_unsupported_synthesis_filter_removes_the_reported_conclusion() -> None:
    answer = (
        "## 版本一致性\n\n"
        "- Walker S2 Edu：国内与海外售卖版本料号相同、配置相同。\n\n"
        "## 核心结论\n\n"
        "Walker S2 Edu 本质上是工业版基础上的增强版，整体性价比更高。"
    )

    filtered = qa_api.strip_unsupported_synthesis(answer)

    assert "版本一致性" in filtered
    assert "料号相同、配置相同" in filtered
    assert "核心结论" not in filtered
    assert "本质上" not in filtered
    assert "性价比更高" not in filtered


def test_unsupported_synthesis_stream_filter_handles_split_heading() -> None:
    stream_filter = qa_api.UnsupportedSynthesisStreamFilter()

    chunks = [
        stream_filter.feed("已明确的配置差异。\n\n核"),
        stream_filter.feed("心结论\nWalker S2 Edu 本质上是增强版。"),
        stream_filter.finish(),
    ]

    visible = "".join(chunks)
    assert visible == "已明确的配置差异。\n\n"


def test_unsupported_synthesis_preserves_wiki_backed_comparison_sections() -> None:
    answer = (
        "【1. 性价比】Edu 版是多执行器、多感知、多场景教学实训平台，整体性价比更高。\n"
        "硬件方面，Edu 版配置 3 个末端执行器 + 1 对腕部相机。\n\n"
        "【2. 定位差异】\n"
        "工业版定位是\"工业任务执行产品\"，核心是把搬运、巡检、操作等具体任务做通。\n\n"
        "【4. 绑定事项】只卖设备容易变成展示品，绑定课程、实训和平台，才能真正用起来。"
    )

    filtered = qa_api.strip_unsupported_synthesis(answer)

    assert filtered == answer


def test_unsupported_synthesis_strips_evidence_disclaimers() -> None:
    answer = (
        "Edu 版配置 3 个末端执行器 + 1 对腕部相机。\n\n"
        "以上内容仅来自 wiki，没有证据支持，仅供参考。"
    )

    filtered = qa_api.strip_unsupported_synthesis(answer)

    assert "末端执行器" in filtered
    assert "以上内容仅来自" not in filtered
    assert "没有证据" not in filtered

    english = (
        "Edu includes additional teaching tools.\n\n"
        "Note: the above information is not backed by any evidence."
    )
    assert "teaching tools" in qa_api.strip_unsupported_synthesis(english)
    assert "not backed" not in qa_api.strip_unsupported_synthesis(english)


def test_unsupported_synthesis_strips_official_framing() -> None:
    answer = (
        "根据官方文档，关于 Walker S2 工业版与 Walker S2 Edu 探索者的价格差异，"
        "文档中仅记录了以下销售口径：\n\n"
        "Walker S2 工业版：标准工业配置定价。\n"
        "Walker S2 Edu 探索者：多执行器、多感知、多场景，官方称性价比更高（无量化对比数据）。\n"
        "文档同时说明，Edu 版虽然定价可能不同，但作为多执行器、多感知、多场景的教学实训平台，"
        "官方口径强调其整体性价比更高。\n"
        "除上述销售口径外，文档未提供两个版本的具体价格或量化对比数据。"
    )

    filtered = qa_api.strip_unsupported_synthesis(answer)

    assert "官方口径" not in filtered
    assert "销售口径" not in filtered
    assert "官方称" not in filtered
    assert "无量化对比数据" not in filtered
    assert "文档中仅记录" not in filtered
    assert "标准工业配置定价" in filtered


@pytest.mark.anyio
async def test_deepseek_stream_never_exposes_unsupported_synthesis(
    monkeypatch, tmp_path: Path
) -> None:
    write(tmp_path / "index.md", "[[walker]]")
    write(tmp_path / "concepts" / "walker.md", "Walker S2 directly supported evidence")
    monkeypatch.setattr(qa_api, "get_team_config", lambda _team: FakeTeamConfig(tmp_path))
    class UnsupportedConclusionProvider(FakeDeepSeekClient):
        def stream(self, system: str, user: str):
            self.calls.append((system, user))
            yield "## 版本一致性\n\n配置相同。\n\n核"
            yield "心结论\n\nWalker S2 Edu 本质上是增强版，性价比更高。"

    monkeypatch.setattr(qa_api, "DeepSeekClient", UnsupportedConclusionProvider)
    visible: list[str] = []

    async def on_token(text: str) -> None:
        visible.append(text)

    answer = await qa_api._retrieve_and_stream(
        "Walker S2 版本有什么区别？",
        team="walker_s2",
        language="zh-CN",
        history=(),
        on_token=on_token,
    )

    streamed = "".join(visible)
    assert "配置相同" in streamed
    assert "配置相同" in answer
    assert "核心结论" not in streamed
    assert "核心结论" not in answer
    assert "本质上" not in streamed
    assert "本质上" not in answer
    assert "性价比更高" not in streamed
    assert "性价比更高" not in answer


def test_public_qa_manager_has_no_claude_code_answer_path() -> None:
    manager_source = (
        Path(__file__).resolve().parents[1] / "worker" / "manager.py"
    ).read_text(encoding="utf-8")

    assert "run_qa_api_stream(" in manager_source
    assert "run_qa_api(" in manager_source
    assert "claude_runner" not in manager_source
    assert "run_claude_stream(" not in manager_source


def test_reference_filter_removes_internal_refs_but_preserves_links_and_images(
    tmp_path: Path,
) -> None:
    write(tmp_path / "index.md", "[[rosa-2-0]]")
    write(tmp_path / "concepts" / "rosa-2-0.md", "ROSA evidence")
    wiki = qa_api.Wiki(tmp_path)
    text = (
        "Answer [rosa-2-0] 【rosa-2-0】 [rosa-2-0 (concepts/rosa-2-0.md)]. "
        "Hide /home/worker/wiki/entities/rosa-2-0.md and README.md. "
        "Keep [official site](https://example.com), https://example.com/guide.md, "
        "and ![diagram](media/robot.png).\n\n"
        "参考资料：\n- [rosa-2-0 (concepts/rosa-2-0.md)]"
    )

    filtered = qa_api.strip_retrieval_references(text, wiki)

    assert "rosa-2-0.md" not in filtered
    assert "README.md" not in filtered
    assert "[rosa-2-0]" not in filtered
    assert "【rosa-2-0】" not in filtered
    assert "参考资料" not in filtered
    assert "[official site](https://example.com)" in filtered
    assert "https://example.com/guide.md" in filtered
    assert "![diagram](media/robot.png)" in filtered


def test_stream_filter_hides_reference_split_across_chunks(tmp_path: Path) -> None:
    write(tmp_path / "index.md", "[[walker]]")
    write(tmp_path / "concepts" / "walker.md", "Walker evidence")
    stream_filter = qa_api.RetrievalReferenceStreamFilter(qa_api.Wiki(tmp_path))

    chunks = [
        stream_filter.feed("A" * 600 + " [wal"),
        stream_filter.feed("ker (concepts/walker.md)] 【wal"),
        stream_filter.feed("ker】 conclusion.\nReferences:\n"),
        stream_filter.feed("- [walker]"),
        stream_filter.finish(),
    ]
    visible = "".join(chunks)

    assert visible.startswith("A" * 600)
    assert "walker" not in visible
    assert "concepts/" not in visible
    assert "References" not in visible


def test_stream_filter_releases_complete_phrases_without_waiting_for_512_chars(
    tmp_path: Path,
) -> None:
    write(tmp_path / "index.md", "[[walker]]")
    write(tmp_path / "concepts" / "walker.md", "Walker evidence")
    stream_filter = qa_api.RetrievalReferenceStreamFilter(qa_api.Wiki(tmp_path))

    first = stream_filter.feed("First sentence. ")
    second = stream_filter.feed("第二句，后面仍在生成")
    tail = stream_filter.finish()

    assert first == "First sentence."
    assert second == " 第二句，"
    assert tail == "后面仍在生成"


def test_stream_filter_keeps_split_reference_private_until_closed(
    tmp_path: Path,
) -> None:
    write(tmp_path / "index.md", "[[walker]]")
    write(tmp_path / "concepts" / "walker.md", "Walker evidence")
    stream_filter = qa_api.RetrievalReferenceStreamFilter(qa_api.Wiki(tmp_path))

    assert stream_filter.feed("Answer [walker (concepts/walker.") == ""
    visible = stream_filter.feed("md)]. Next sentence. ") + stream_filter.finish()

    assert "walker" not in visible
    assert ".md" not in visible
    assert "Next sentence." in visible


@pytest.mark.anyio
async def test_deepseek_router_mismatch_uses_deterministic_wiki_fallback(
    monkeypatch, tmp_path: Path
) -> None:
    write(tmp_path / "index.md", "[[walker-s2]] [[unrelated]]")
    write(tmp_path / "concepts" / "walker-s2.md", "Walker S2 navigation evidence")
    write(tmp_path / "concepts" / "unrelated.md", "Unrelated product")
    monkeypatch.setattr(qa_api, "get_team_config", lambda _team: FakeTeamConfig(tmp_path))
    captured_prompts: list[str] = []

    class CapturingProvider(FakeDeepSeekClient):
        def complete(self, system: str, user: str) -> str:
            if "Intent Planner" in system:
                return super().complete(system, user)
            self.calls.append((system, user))
            return (
                '{"scope_consistency":{"valid":true,'
                '"unsupported_cross_scope_transfer":[]},"planner_faithful":true,'
                '"unsupported_assumptions":[],"corrected_standalone_question":null,'
                '"primary_solution":"Walker S2 navigation","selected_pages":'
                '["concepts/walker-s2.md"],"selected_images":[],'
                '"need_more_search":false,"additional_search_queries":[],'
                '"uncertainties_to_check":[],"direct_answer_plan":"Explain navigation.",'
                '"supporting_points":[]}'
            )

        def stream(self, _system: str, user: str):
            captured_prompts.append(user)
            yield "deepseek answer"

    monkeypatch.setattr(qa_api, "DeepSeekClient", CapturingProvider)

    async def discard(_text: str) -> None:
        return None

    answer = await qa_api._retrieve_and_stream(
        "What navigation capabilities does Walker S2 have?",
        team="walker_s2",
        language="en",
        history=(),
        on_token=discard,
    )

    assert answer == "deepseek answer"
    assert "Walker S2 navigation evidence" in captured_prompts[0]
    assert "Unrelated product" not in captured_prompts[0]


@pytest.mark.anyio
async def test_local_wiki_failure_does_not_invoke_deepseek(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(qa_api, "get_team_config", lambda _team: FakeTeamConfig(tmp_path))
    calls: list[str] = []
    monkeypatch.setattr(qa_api, "DeepSeekClient", lambda: calls.append("deepseek"))

    async def discard(_text: str) -> None:
        return None

    with pytest.raises(FileNotFoundError):
        await qa_api._retrieve_and_stream(
            "What is Walker?",
            team="walker_s2",
            language="en",
            history=(),
            on_token=discard,
        )

    assert calls == []
