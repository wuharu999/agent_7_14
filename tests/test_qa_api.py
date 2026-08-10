from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from worker import qa_api
from worker.claude_runner import AI_NOTICE_RESPONSES, GENERIC_ERROR_RESPONSES
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
    assert [document.text for document in wiki.load(["hidden", "same"])] == ["a", "b"]


def test_router_response_rejects_unknown_duplicate_and_excess_slugs() -> None:
    result = qa_api.parse_router_response(
        '{"pages":["a","unknown","a","b","c","d","e","f"]}',
        {"a", "b", "c", "d", "e", "f"},
    )
    assert result == ["a", "b", "c", "d", "e"]


@dataclass
class FakeTeamConfig:
    wiki_dir: Path


class FakeCerebrasClient:
    instances: list["FakeCerebrasClient"] = []

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
