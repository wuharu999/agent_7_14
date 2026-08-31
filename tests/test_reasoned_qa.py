from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("langgraph")

from worker import reasoned_qa


class FakeProvider:
    timeout = 10

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        if "plan bounded" in system:
            return json.dumps(
                {
                    "standalone_question": "How do I teleoperate the robot?",
                    "intent": "how_to",
                    "preferred_abstraction": "application_or_workflow",
                    "search_queries": ["teleoperation"],
                }
            )
        return json.dumps(
            {
                "selected_pages": ["entities/teleoperation-platform.md"],
                "need_more_search": False,
                "additional_search_queries": [],
                "answer_plan": {"direct_answer": "Use the documented platform."},
            }
        )

    def stream(self, system: str, user: str):
        self.calls.append((system, user))
        assert "entities/teleoperation-platform.md" in user
        yield "Use ThinkerStudio for teleoperation."


@pytest.mark.anyio
async def test_reasoned_graph_uses_bounded_local_search_then_streams_final_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki = tmp_path / "wiki"
    page = wiki / "entities" / "teleoperation-platform.md"
    page.parent.mkdir(parents=True)
    page.write_text(
        "---\ntitle: ThinkerStudio\ntags: teleoperation\n---\n"
        "# ThinkerStudio\nThinkerStudio is the documented teleoperation platform.",
        encoding="utf-8",
    )
    monkeypatch.setattr(reasoned_qa, "WORKER_ROOT_DIR", tmp_path / "worker-runtime")
    provider = FakeProvider()
    chunks: list[str] = []

    async def on_token(token: str) -> None:
        chunks.append(token)

    answer = await reasoned_qa.run_reasoned_qa_stream(
        question="How do I teleoperate the robot?",
        team="all",
        language="en",
        history=(),
        wiki_root=wiki,
        provider=provider,
        on_token=on_token,
    )

    assert answer == "Use ThinkerStudio for teleoperation."
    assert "".join(chunks) == answer
    assert len(provider.calls) == 3
    generated = list((tmp_path / "worker-runtime" / ".agent1-worker" / "qa-architect").rglob("search.db"))
    assert len(generated) == 1


def test_reasoned_graph_is_an_explicit_optional_dependency() -> None:
    assert isinstance(reasoned_qa.langgraph_available(), bool)
