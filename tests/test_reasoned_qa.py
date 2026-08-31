from __future__ import annotations

import asyncio
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


class GenericQuestionProvider:
    timeout = 10

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        if "plan bounded" not in system:
            return json.dumps(
                {
                    "selected_pages": ["entities/developer-tools.md"],
                    "need_more_search": False,
                    "planner_faithful": True,
                    "scope_consistency": {"valid": True},
                    "uncertainties_to_check": [],
                    "primary_solution": "Developer tools",
                    "direct_answer_plan": "Use the documented tools.",
                    "supporting_points": [],
                }
            )
        return json.dumps(
            {
                "standalone_question": "What developer tools are available?",
                "scope_analysis": {
                    "relation": "out_of_scope",
                    "explicit_entities": [],
                },
                "intent": "how_to",
                "preferred_abstraction": "application_or_workflow",
                "search_queries": ["developer tools"],
            }
        )

    def stream(self, system: str, user: str):
        self.calls.append((system, user))
        assert "entities/developer-tools.md" in user
        yield "The documented developer tools are available in this knowledge base."


@pytest.mark.anyio
async def test_reasoned_graph_does_not_reject_a_generic_question_for_the_selected_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki = tmp_path / "wiki"
    page = wiki / "entities" / "developer-tools.md"
    page.parent.mkdir(parents=True)
    page.write_text("# Developer tools\nDocumented developer tools.", encoding="utf-8")
    monkeypatch.setattr(reasoned_qa, "WORKER_ROOT_DIR", tmp_path / "worker-runtime")
    provider = GenericQuestionProvider()
    chunks: list[str] = []

    async def on_token(token: str) -> None:
        chunks.append(token)

    answer = await reasoned_qa.run_reasoned_qa_stream(
        question="What developer tools are available?",
        team="tian_gong",
        language="en",
        history=(),
        wiki_root=wiki,
        provider=provider,
        on_token=on_token,
    )

    assert answer == "The documented developer tools are available in this knowledge base."
    assert chunks == [answer]
    assert len(provider.calls) == 3
    source = Path(reasoned_qa.__file__).read_text(encoding="utf-8")
    assert "_scope_stop_message" not in source
    assert "another product" not in source


class SecondRoundProvider(FakeProvider):
    def __init__(self) -> None:
        super().__init__()
        self.reason_requests = 0

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        if "plan bounded" in system:
            return json.dumps(
                {
                    "standalone_question": "How do I teleoperate the robot?",
                    "scope_analysis": {"relation": "in_scope"},
                    "intent": "how_to",
                    "preferred_abstraction": "application_or_workflow",
                    "search_queries": ["teleoperation"],
                }
            )
        self.reason_requests += 1
        return json.dumps(
            {
                "selected_pages": ["entities/teleoperation-platform.md"],
                "need_more_search": self.reason_requests == 1,
                "additional_search_queries": ["teleoperation"] if self.reason_requests == 1 else [],
                "planner_faithful": True,
                "scope_consistency": {"valid": True},
                "uncertainties_to_check": [],
                "primary_solution": "ThinkerStudio",
                "direct_answer_plan": "Use the documented platform.",
                "supporting_points": ["Use the documented platform."],
            }
        )


@pytest.mark.anyio
async def test_reasoned_graph_preserves_the_source_project_second_search_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki = tmp_path / "wiki"
    page = wiki / "entities" / "teleoperation-platform.md"
    page.parent.mkdir(parents=True)
    page.write_text("# ThinkerStudio\nTeleoperation platform.", encoding="utf-8")
    monkeypatch.setattr(reasoned_qa, "WORKER_ROOT_DIR", tmp_path / "worker-runtime")
    provider = SecondRoundProvider()

    async def on_token(_token: str) -> None:
        await asyncio.sleep(0)

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
    assert provider.reason_requests == 2
    assert len(provider.calls) == 4
