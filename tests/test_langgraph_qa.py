from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

pytest.importorskip("langgraph")

from worker.langgraph_qa import interface


def _planner(*, relation: str = "in_scope") -> str:
    return json.dumps(
        {
            "scope_analysis": {
                "active_scope": "ignored-provider-value",
                "explicit_entities": [],
                "resolved_references": [],
                "relation": relation,
                "reason": "classification",
                "confidence": 0.8,
            },
            "topic_relation": "ambiguous",
            "current_subject": None,
            "history_used": [],
            "history_ignored": [],
            "standalone_question": "How do I teleoperate the robot?",
            "intent": "how_to",
            "preferred_abstraction": "application_or_workflow",
            "search_queries": ["teleoperation"],
        }
    )


def _reason(*, need_more: bool = False) -> str:
    return json.dumps(
        {
            "scope_consistency": {
                "valid": True,
                "unsupported_cross_scope_transfer": [],
            },
            "planner_faithful": True,
            "unsupported_assumptions": [],
            "corrected_standalone_question": None,
            "primary_solution": "ThinkerStudio",
            "selected_pages": ["entities/teleoperation-platform.md"],
            "selected_images": [],
            "need_more_search": need_more,
            "additional_search_queries": ["teleoperation"] if need_more else [],
            "uncertainties_to_check": [],
            "direct_answer_plan": "Use the documented platform.",
            "supporting_points": ["Follow its teleoperation workflow."],
        }
    )


class FakeProvider:
    timeout = 10

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.reason_calls = 0

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        if "Intent Planner" in system:
            return _planner()
        self.reason_calls += 1
        return _reason()

    def stream(self, system: str, user: str):
        self.calls.append((system, user))
        assert "ThinkerStudio is the documented teleoperation platform" in user
        yield "Use ThinkerStudio "
        yield "for teleoperation."


def _wiki(tmp_path: Path) -> Path:
    wiki = tmp_path / "wiki"
    page = wiki / "entities" / "teleoperation-platform.md"
    page.parent.mkdir(parents=True)
    page.write_text(
        "---\ntitle: ThinkerStudio\ntags: [teleoperation]\n---\n"
        "# ThinkerStudio\nThinkerStudio is the documented teleoperation platform.",
        encoding="utf-8",
    )
    return wiki


@pytest.mark.anyio
async def test_ported_graph_indexes_selected_wiki_and_streams_final_answer(
    tmp_path: Path,
) -> None:
    wiki = _wiki(tmp_path)
    provider = FakeProvider()
    chunks: list[str] = []

    async def on_token(token: str) -> None:
        chunks.append(token)

    answer = await interface.stream_answer(
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
    artifacts = list((tmp_path / ".agent1-worker" / "qa-langgraph").rglob("search.db"))
    assert len(artifacts) == 1


@pytest.mark.anyio
async def test_selected_scope_does_not_hard_reject_generic_question(
    tmp_path: Path,
) -> None:
    wiki = _wiki(tmp_path)

    class OutOfScopeProvider(FakeProvider):
        def complete(self, system: str, user: str) -> str:
            self.calls.append((system, user))
            if "Intent Planner" in system:
                return _planner(relation="out_of_scope")
            self.reason_calls += 1
            return _reason()

    provider = OutOfScopeProvider()
    chunks: list[str] = []

    async def on_token(token: str) -> None:
        chunks.append(token)

    answer = await interface.stream_answer(
        question="What developer tools are available?",
        team="tian_gong",
        language="en",
        history=(),
        wiki_root=wiki,
        provider=provider,
        on_token=on_token,
    )

    assert answer == "Use ThinkerStudio for teleoperation."
    assert "".join(chunks) == answer
    assert len(provider.calls) == 3


@pytest.mark.anyio
async def test_ported_graph_allows_only_one_additional_search_round(
    tmp_path: Path,
) -> None:
    wiki = _wiki(tmp_path)

    class SecondRoundProvider(FakeProvider):
        def complete(self, system: str, user: str) -> str:
            self.calls.append((system, user))
            if "Intent Planner" in system:
                return _planner()
            self.reason_calls += 1
            return _reason(need_more=self.reason_calls == 1)

    provider = SecondRoundProvider()

    async def discard(_token: str) -> None:
        await asyncio.sleep(0)

    answer = await interface.stream_answer(
        question="How do I teleoperate the robot?",
        team="all",
        language="en",
        history=(),
        wiki_root=wiki,
        provider=provider,
        on_token=discard,
    )

    assert answer == "Use ThinkerStudio for teleoperation."
    assert provider.reason_calls == 2
    assert len(provider.calls) == 4


def test_worker_uses_ported_module_and_python_310_stream_timeout() -> None:
    root = Path(__file__).resolve().parents[1]
    qa_api_source = (root / "worker" / "qa_api.py").read_text(encoding="utf-8")
    interface_source = (root / "worker" / "langgraph_qa" / "interface.py").read_text(
        encoding="utf-8"
    )

    assert "from worker.langgraph_qa import stream_answer" in qa_api_source
    assert not (root / "worker" / "reasoned_qa.py").exists()
    assert "asyncio.timeout(" not in interface_source
    assert "asyncio.wait_for(" in interface_source


@pytest.mark.anyio
async def test_selected_referenced_image_is_private_until_final_result(
    tmp_path: Path,
) -> None:
    wiki = _wiki(tmp_path)
    page = wiki / "entities" / "teleoperation-platform.md"
    page.write_text(
        page.read_text(encoding="utf-8")
        + "\n## Architecture\n![Architecture workflow diagram](../media/diagram.png)\n",
        encoding="utf-8",
    )
    image = wiki / "media" / "diagram.png"
    image.parent.mkdir()
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)

    class ImageProvider(FakeProvider):
        def complete(self, system: str, user: str) -> str:
            self.calls.append((system, user))
            if "Intent Planner" in system:
                return _planner()
            self.reason_calls += 1
            response = json.loads(_reason())
            response["selected_images"] = [
                {
                    "path": "media/diagram.png",
                    "supports_claim": "Teleoperation architecture",
                    "utility": "high",
                }
            ]
            return json.dumps(response)

    provider = ImageProvider()
    chunks: list[str] = []

    async def on_token(token: str) -> None:
        chunks.append(token)

    answer = await interface.stream_answer(
        question="Show the teleoperation architecture.",
        team="all",
        language="en",
        history=(),
        wiki_root=wiki,
        provider=provider,
        on_token=on_token,
    )

    assert "![Teleoperation architecture](wiki/media/diagram.png)" in answer
    assert "![" not in "".join(chunks)
