from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

import pytest

from worker import qa_api
from worker.egress_region import EgressRegionDecision
from worker.qa_response import AI_NOTICE_RESPONSES, GENERIC_ERROR_RESPONSES
from worker.conversation_store import ConversationTurn
from worker.prompt_security import GuardDecision


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class StaticRegionGate:
    def __init__(self, country_code: str | None = "US", allowed: bool = True) -> None:
        self.result = EgressRegionDecision(
            country_code,
            allowed,
            "allowed_country" if allowed else "blocked_country",
        )

    def decision(self) -> EgressRegionDecision:
        return self.result


@pytest.fixture(autouse=True)
def reset_provider_circuit(monkeypatch) -> Iterator[None]:
    qa_api._CEREBRAS_CIRCUIT.reset()
    monkeypatch.setattr(qa_api, "_CEREBRAS_REGION_GATE", StaticRegionGate())
    yield
    qa_api._CEREBRAS_CIRCUIT.reset()


def test_wiki_reader_only_loads_indexed_pages_and_duplicate_slugs(tmp_path: Path) -> None:
    write(tmp_path / "index.md", "[[allowed]] [[same|Same page]]")
    write(tmp_path / "concepts" / "allowed.md", "allowed")
    write(tmp_path / "concepts" / "hidden.md", "hidden")
    write(tmp_path / "a" / "same.md", "a")
    write(tmp_path / "b" / "same.md", "b")

    wiki = qa_api.Wiki(tmp_path)

    assert wiki.retrievable_slugs == {"allowed", "same"}
    assert [document.text for document in wiki.load(["hidden", "same"])] == ["a", "b"]


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


def test_wiki_reader_canonicalizes_in_memory_without_changing_files(tmp_path: Path) -> None:
    index = tmp_path / "index.md"
    page = tmp_path / "entities" / "tiangong.md"
    write(index, "[[tiangong]] 天工2.0 Pro")
    write(page, "# 天工2.0 Pro\n产品介绍")
    original_index = index.read_bytes()
    original_page = page.read_bytes()

    wiki = qa_api.Wiki(tmp_path)
    documents = wiki.load(["tiangong"])

    assert "天工行者无疆" in wiki.index_text
    assert documents[0].text.startswith("# 天工行者无疆")
    assert index.read_bytes() == original_index
    assert page.read_bytes() == original_page


def test_api_prompts_are_wiki_only_and_canonicalize_all_untrusted_text(
    tmp_path: Path,
) -> None:
    write(tmp_path / "index.md", "[[tiangong]] 天工2.0")
    write(tmp_path / "entities" / "tiangong.md", "天工3.0")
    wiki = qa_api.Wiki(tmp_path)
    history = [ConversationTurn("天工2.0 lite", "天工2.0 Plus")]
    candidates = wiki.candidate_slugs("tian_gong", "天工2.0 Pro")
    router = qa_api._router_prompt(
        "天工2.0 Pro", "tian_gong", history, wiki, candidates
    )
    context = qa_api._make_context(wiki, wiki.load(["tiangong"]))
    answer = qa_api._answer_prompt(
        "天工2.0 Pro",
        team="tian_gong",
        language="zh-CN",
        history=history,
        context=context,
    )

    combined = router + answer
    assert "天工行者无疆" in combined
    assert "天工行者基础版" in combined
    assert "天工行者无界" in combined
    assert "天工行者dex" in combined
    assert "raw/sources" not in combined
    assert "CLAUDE.md" not in combined


def test_router_response_rejects_unknown_duplicate_and_excess_slugs() -> None:
    result = qa_api.parse_router_response(
        '{"pages":["a","unknown","a","b","c","d","e","f"]}',
        {"a", "b", "c", "d", "e", "f"},
    )
    assert result == ["a", "b", "c", "d", "e"]


def test_router_response_safely_normalizes_paths_case_and_markdown() -> None:
    result = qa_api.parse_router_response(
        '{"pages":["wiki/entities/Walker-C1.MD", "[[TIANGONG_PLUS]]", "[S2](concepts/walker-s2.md)"]}',
        {"walker-c1", "tiangong-plus", "walker-s2"},
    )

    assert result == ["walker-c1", "tiangong-plus", "walker-s2"]


def test_walker_c1_topic_supplements_stale_index_without_exposing_unrelated_pages(
    tmp_path: Path,
) -> None:
    write(tmp_path / "index.md", "[[walker-s2]]")
    write(tmp_path / "entities" / "walker-s2.md", "S2 evidence")
    write(tmp_path / "entities" / "walker-c1.md", "C1 evidence")
    write(tmp_path / "entities" / "private-notes.md", "Unrelated")
    wiki = qa_api.Wiki(tmp_path)

    candidates = wiki.candidate_slugs("walker_c1", "我需要 C1 产品介绍")

    assert candidates == {"walker-s2", "walker-c1"}
    assert [
        document.text
        for document in wiki.load(["walker-c1"], allowed_slugs=candidates)
    ] == ["C1 evidence"]
    assert wiki.load(["private-notes"], allowed_slugs=candidates) == []


def test_retrieval_expands_selected_page_with_links_and_related_wiki_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    write(tmp_path / "index.md", "[[walker-overview]] [[battery]] [[charging]] [[unrelated]]")
    write(
        tmp_path / "entities" / "walker-overview.md",
        "# Walker overview\nBattery system [[battery]] and [[charging]].",
    )
    write(tmp_path / "concepts" / "battery.md", "Battery quick-swap details.")
    write(tmp_path / "concepts" / "charging.md", "Charging time and charger details.")
    write(tmp_path / "concepts" / "unrelated.md", "Catering workflow.")
    wiki = qa_api.Wiki(tmp_path)
    monkeypatch.setattr(qa_api, "WIKI_QA_MAX_PAGES", 3)

    expanded = wiki.expand_slugs(
        ["walker-overview"],
        question="Tell me all battery and charging details",
        team="walker_s2",
        history=(),
        allowed_slugs=wiki.retrievable_slugs,
    )

    assert expanded[0] == "walker-overview"
    assert set(expanded[1:]) == {"battery", "charging"}
    assert "unrelated" not in expanded


def test_retrieval_expansion_never_adds_unindexed_or_raw_source_pages(
    tmp_path: Path,
) -> None:
    write(tmp_path / "index.md", "[[primary]] [[indexed]]")
    write(tmp_path / "concepts" / "primary.md", "# Primary\n[[hidden]] [[indexed]]")
    write(tmp_path / "concepts" / "indexed.md", "Indexed evidence")
    write(tmp_path / "concepts" / "hidden.md", "Hidden evidence")
    wiki = qa_api.Wiki(tmp_path)

    expanded = wiki.expand_slugs(
        ["primary"],
        question="evidence",
        team="walker_s2",
        history=(),
        allowed_slugs=wiki.retrievable_slugs,
    )

    assert expanded == ["primary", "indexed"]
    assert "hidden" not in expanded


@dataclass
class FakeTeamConfig:
    wiki_dir: Path


class FakeCerebrasClient:
    instances: ClassVar[list[FakeCerebrasClient]] = []
    timeout = 2

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.__class__.instances.append(self)

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        return '{"pages":["walker"]}'

    def stream(self, system: str, user: str):
        self.calls.append((system, user))
        yield "平台名称是慧思"
        yield "开物平台。"


@pytest.mark.anyio
async def test_streaming_works_without_python_311_asyncio_timeout(monkeypatch) -> None:
    monkeypatch.delattr(asyncio, "timeout", raising=False)
    chunks: list[str] = []

    async def on_token(token: str) -> None:
        chunks.append(token)

    answer = await qa_api._stream_in_thread(iter(("one", "two")), on_token)

    assert answer == "onetwo"
    assert chunks == ["one", "two"]


@pytest.mark.anyio
async def test_retrieval_api_preserves_language_team_history_and_response_boundary(
    monkeypatch, tmp_path: Path
) -> None:
    write(tmp_path / "index.md", "[[walker]] [[unrelated]]")
    write(tmp_path / "concepts" / "walker.md", "Walker evidence")
    write(tmp_path / "concepts" / "unrelated.md", "Other evidence")
    FakeCerebrasClient.instances.clear()
    monkeypatch.setattr(qa_api, "CerebrasClient", FakeCerebrasClient)
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

    client = FakeCerebrasClient.instances[0]
    router_prompt = client.calls[0][1]
    answer_prompt = client.calls[1][1]
    assert "SELECTED ROBOT OR TOPIC: walker_s2" in router_prompt
    assert "它是什么？" in router_prompt
    assert "RETRIEVABLE PAGE SLUGS" in router_prompt
    assert "ANSWER LANGUAGE: Simplified Chinese (简体中文)" in answer_prompt
    assert "Walker evidence" in answer_prompt
    assert "Other evidence" not in answer_prompt
    assert "concepts/walker.md" not in answer_prompt
    assert "WIKI PAGE: walker" not in answer_prompt
    assert answer == (
        "平台名称是Thinkerstudio遥操数采平台。\n\n"
        + AI_NOTICE_RESPONSES["zh-CN"]
    )
    assert "".join(chunks) == answer
    assert "慧思开物" not in "".join(chunks)


@pytest.mark.anyio
async def test_cerebras_failure_becomes_localized_user_safe_response(monkeypatch) -> None:
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
async def test_predefined_response_does_not_call_cerebras(monkeypatch) -> None:
    async def forbidden(*_args, **_kwargs):
        raise AssertionError("predefined response must not call Cerebras")

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


def test_answer_system_preserves_existing_prompt_contract() -> None:
    assert "only the supplied Wiki pages" in qa_api.ANSWER_SYSTEM
    assert "political" in qa_api.ANSWER_SYSTEM
    assert "exactly as written" in qa_api.ANSWER_SYSTEM
    assert "[KNOWLEDGE_GAP]" in qa_api.ANSWER_SYSTEM
    assert "Do not mention tools" in qa_api.ANSWER_SYSTEM
    assert "Never include citations" in qa_api.ANSWER_SYSTEM
    assert "Do not output image paths or image Markdown" in qa_api.ANSWER_SYSTEM
    assert "Cite factual statements" not in qa_api.ANSWER_SYSTEM
    assert qa_api.DeepSeekClient._options() == {
        "temperature": 0,
        "extra_body": {"thinking": {"type": "disabled"}},
    }


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


def test_circuit_breaker_uses_one_probe_and_recovers() -> None:
    now = [100.0]
    circuit = qa_api.ProviderCircuitBreaker(300, clock=lambda: now[0])
    first = circuit.select()
    circuit.failure(first)

    assert circuit.select().provider == "deepseek"
    now[0] = 401.0
    probe = circuit.select()
    concurrent = circuit.select()
    assert probe == qa_api.CircuitDecision("cerebras", 1, probe=True)
    assert concurrent.provider == "deepseek"

    circuit.success(probe)
    assert circuit.select().provider == "cerebras"


class FakeProvider:
    timeout = 2

    def __init__(
        self,
        name: str,
        calls: list[str],
        *,
        fail_complete: bool = False,
        fail_stream: bool = False,
        empty_stream: bool = False,
        router_response: str = '{"pages":["walker"]}',
    ) -> None:
        self.name = name
        self.calls = calls
        self.fail_complete = fail_complete
        self.fail_stream = fail_stream
        self.empty_stream = empty_stream
        self.router_response = router_response

    def complete(self, _system: str, _user: str) -> str:
        self.calls.append(f"{self.name}:complete")
        if self.fail_complete:
            raise RuntimeError("provider unavailable")
        return self.router_response

    def stream(self, _system: str, _user: str):
        self.calls.append(f"{self.name}:stream")
        if self.empty_stream:
            return
        yield f"{self.name} answer"
        if self.fail_stream:
            yield ". " + "x" * 700
            raise RuntimeError("stream interrupted")


@pytest.mark.anyio
async def test_cerebras_failure_retries_complete_request_with_deepseek(
    monkeypatch, tmp_path: Path
) -> None:
    write(tmp_path / "index.md", "[[walker]]")
    write(tmp_path / "concepts" / "walker.md", "Walker evidence")
    monkeypatch.setattr(qa_api, "get_team_config", lambda _team: FakeTeamConfig(tmp_path))
    calls: list[str] = []

    def provider(name: str):
        return FakeProvider(name, calls, fail_complete=name == "cerebras")

    monkeypatch.setattr(qa_api, "_provider_client", provider)
    chunks: list[str] = []
    resets: list[bool] = []

    async def on_token(text: str) -> None:
        chunks.append(text)

    async def on_reset() -> None:
        chunks.clear()
        resets.append(True)

    answer = await qa_api._retrieve_and_stream(
        "What is Walker?",
        team="walker_s2",
        language="en",
        history=(),
        on_token=on_token,
        on_reset=on_reset,
    )

    assert answer == "deepseek answer"
    assert "".join(chunks) == answer
    assert resets == [True]
    assert calls == ["cerebras:complete", "deepseek:complete", "deepseek:stream"]
    assert qa_api._CEREBRAS_CIRCUIT.select().provider == "deepseek"


@pytest.mark.anyio
async def test_empty_cerebras_answer_is_malformed_and_uses_deepseek(
    monkeypatch, tmp_path: Path
) -> None:
    write(tmp_path / "index.md", "[[walker]]")
    write(tmp_path / "concepts" / "walker.md", "Walker evidence")
    monkeypatch.setattr(qa_api, "get_team_config", lambda _team: FakeTeamConfig(tmp_path))
    calls: list[str] = []

    def provider(name: str):
        return FakeProvider(name, calls, empty_stream=name == "cerebras")

    monkeypatch.setattr(qa_api, "_provider_client", provider)

    async def discard(_text: str) -> None:
        return None

    answer = await qa_api._retrieve_and_stream(
        "What is Walker?",
        team="walker_s2",
        language="en",
        history=(),
        on_token=discard,
        on_reset=lambda: discard(""),
    )

    assert answer == "deepseek answer"
    assert calls == [
        "cerebras:complete",
        "cerebras:stream",
        "deepseek:complete",
        "deepseek:stream",
    ]


@pytest.mark.anyio
async def test_midstream_failure_clears_primary_text_before_deepseek(
    monkeypatch, tmp_path: Path
) -> None:
    write(tmp_path / "index.md", "[[walker]]")
    write(tmp_path / "concepts" / "walker.md", "Walker evidence")
    monkeypatch.setattr(qa_api, "get_team_config", lambda _team: FakeTeamConfig(tmp_path))
    calls: list[str] = []

    def provider(name: str):
        return FakeProvider(name, calls, fail_stream=name == "cerebras")

    monkeypatch.setattr(qa_api, "_provider_client", provider)
    visible: list[str] = []
    reset_snapshots: list[str] = []

    async def on_token(text: str) -> None:
        visible.append(text)

    async def on_reset() -> None:
        reset_snapshots.append("".join(visible))
        visible.clear()

    answer = await qa_api._retrieve_and_stream(
        "What is Walker?",
        team="walker_s2",
        language="en",
        history=(),
        on_token=on_token,
        on_reset=on_reset,
    )

    assert reset_snapshots and "cerebras answer" in reset_snapshots[0]
    assert answer == "deepseek answer"
    assert "".join(visible) == "deepseek answer"
    assert calls == [
        "cerebras:complete",
        "cerebras:stream",
        "deepseek:complete",
        "deepseek:stream",
    ]


@pytest.mark.anyio
async def test_open_circuit_bypasses_cerebras(monkeypatch, tmp_path: Path) -> None:
    write(tmp_path / "index.md", "[[walker]]")
    write(tmp_path / "concepts" / "walker.md", "Walker evidence")
    monkeypatch.setattr(qa_api, "get_team_config", lambda _team: FakeTeamConfig(tmp_path))
    calls: list[str] = []
    monkeypatch.setattr(
        qa_api,
        "_provider_client",
        lambda name: FakeProvider(name, calls),
    )
    decision = qa_api._CEREBRAS_CIRCUIT.select()
    qa_api._CEREBRAS_CIRCUIT.failure(decision)

    async def discard(_text: str) -> None:
        return None

    answer = await qa_api._retrieve_and_stream(
        "What is Walker?",
        team="walker_s2",
        language="en",
        history=(),
        on_token=discard,
        on_reset=lambda: discard(""),
    )

    assert answer == "deepseek answer"
    assert calls == ["deepseek:complete", "deepseek:stream"]


@pytest.mark.anyio
@pytest.mark.parametrize("country_code", ["CN", "TW", "HK", "SG"])
async def test_blocked_egress_region_never_initializes_cerebras(
    monkeypatch, tmp_path: Path, country_code: str
) -> None:
    write(tmp_path / "index.md", "[[walker]]")
    write(tmp_path / "concepts" / "walker.md", "Walker evidence")
    monkeypatch.setattr(qa_api, "get_team_config", lambda _team: FakeTeamConfig(tmp_path))
    monkeypatch.setattr(
        qa_api, "_CEREBRAS_REGION_GATE", StaticRegionGate(country_code, False)
    )
    calls: list[str] = []
    monkeypatch.setattr(
        qa_api,
        "_provider_client",
        lambda name: FakeProvider(name, calls),
    )

    async def discard(_text: str) -> None:
        return None

    answer = await qa_api._retrieve_and_stream(
        "What is Walker?",
        team="walker_s2",
        language="en",
        history=(),
        on_token=discard,
        on_reset=lambda: discard(""),
    )

    assert answer == "deepseek answer"
    assert calls == ["deepseek:complete", "deepseek:stream"]


@pytest.mark.anyio
async def test_deepseek_router_mismatch_uses_deterministic_wiki_fallback(
    monkeypatch, tmp_path: Path
) -> None:
    write(tmp_path / "index.md", "[[walker-s2]] [[unrelated]]")
    write(tmp_path / "concepts" / "walker-s2.md", "Walker S2 navigation evidence")
    write(tmp_path / "concepts" / "unrelated.md", "Unrelated product")
    monkeypatch.setattr(qa_api, "get_team_config", lambda _team: FakeTeamConfig(tmp_path))
    monkeypatch.setattr(
        qa_api, "_CEREBRAS_REGION_GATE", StaticRegionGate("CN", False)
    )
    calls: list[str] = []
    captured_prompts: list[str] = []

    class CapturingProvider(FakeProvider):
        def stream(self, _system: str, user: str):
            captured_prompts.append(user)
            yield "deepseek answer"

    monkeypatch.setattr(
        qa_api,
        "_provider_client",
        lambda name: CapturingProvider(
            name,
            calls,
            router_response='{"pages":["invented-page-that-does-not-exist"]}',
        ),
    )

    async def discard(_text: str) -> None:
        return None

    answer = await qa_api._retrieve_and_stream(
        "What navigation capabilities does Walker S2 have?",
        team="walker_s2",
        language="en",
        history=(),
        on_token=discard,
        on_reset=lambda: discard(""),
    )

    assert answer == "deepseek answer"
    assert "Walker S2 navigation evidence" in captured_prompts[0]
    assert "Unrelated product" not in captured_prompts[0]


@pytest.mark.anyio
async def test_local_wiki_failure_does_not_invoke_either_provider(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(qa_api, "get_team_config", lambda _team: FakeTeamConfig(tmp_path))
    calls: list[str] = []
    monkeypatch.setattr(
        qa_api,
        "_provider_client",
        lambda name: calls.append(name),
    )

    async def discard(_text: str) -> None:
        return None

    with pytest.raises(FileNotFoundError):
        await qa_api._retrieve_and_stream(
            "What is Walker?",
            team="walker_s2",
            language="en",
            history=(),
            on_token=discard,
            on_reset=lambda: discard(""),
        )

    assert calls == []


@pytest.mark.anyio
async def test_both_provider_failures_return_only_localized_generic_response(
    monkeypatch, tmp_path: Path
) -> None:
    write(tmp_path / "index.md", "[[walker]]")
    write(tmp_path / "concepts" / "walker.md", "Walker evidence")
    monkeypatch.setattr(qa_api, "get_team_config", lambda _team: FakeTeamConfig(tmp_path))
    calls: list[str] = []
    monkeypatch.setattr(
        qa_api,
        "_provider_client",
        lambda name: FakeProvider(name, calls, fail_complete=True),
    )
    chunks: list[str] = []

    async def on_chunk(text: str, _thinking: str, _tokens: int) -> None:
        chunks.append(text)

    answer = await qa_api.run_qa_api_stream(
        "What is Walker?",
        team="walker_s2",
        language="en",
        on_chunk=on_chunk,
        guard_decision=GuardDecision(False, "none", "en"),
    )

    assert answer == GENERIC_ERROR_RESPONSES["en"] + "\n\n" + AI_NOTICE_RESPONSES["en"]
    assert chunks == [answer]
    assert calls == ["cerebras:complete", "deepseek:complete"]
    assert "provider unavailable" not in answer


@pytest.mark.anyio
async def test_public_stream_replaces_partial_primary_answer_before_fallback(
    monkeypatch,
) -> None:
    primary = "Primary partial " + "x" * 600

    async def failover(*_args, on_token, on_reset, **_kwargs):
        await on_token(primary)
        await on_reset()
        await on_token("Fallback answer")
        return "Fallback answer"

    monkeypatch.setattr(qa_api, "_retrieve_and_stream", failover)
    chunks: list[str] = []
    replacements: list[str] = []

    async def on_chunk(text: str, _thinking: str, _tokens: int) -> None:
        chunks.append(text)

    async def on_replace(text: str) -> None:
        replacements.append(text)

    answer = await qa_api.run_qa_api_stream(
        "What is Walker?",
        team="walker_s2",
        language="en",
        on_chunk=on_chunk,
        on_replace=on_replace,
        guard_decision=GuardDecision(False, "none", "en"),
    )

    assert replacements == [""]
    assert chunks[0].startswith("Primary partial")
    assert chunks[-1].startswith("Fallback answer")
    assert answer == "Fallback answer\n\n" + AI_NOTICE_RESPONSES["en"]
