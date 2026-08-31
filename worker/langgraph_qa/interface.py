from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Iterator, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import quote

from worker.config import (
    QA_REASONING_MAX_CANDIDATES,
    QA_REASONING_MAX_PAGES,
    QA_STRICT_ROBOT_SCOPE,
    WIKI_QA_MAX_PAGE_CHARS,
)
from worker.langgraph_qa.qa.graph import run_qa_pipeline
from worker.langgraph_qa.runtime import (
    ChatProvider,
    Runtime,
    artifact_dir_for,
    bind_runtime,
    ensure_artifacts,
)
from worker.topic_policy import resolve_topic


log = logging.getLogger(__name__)

def _history_messages(history: Sequence[Any]) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for turn in history:
        if isinstance(turn, dict):
            question = str(turn.get("question", "")).strip()
            answer = str(turn.get("answer", "")).strip()
        else:
            question = str(getattr(turn, "question", "")).strip()
            answer = str(getattr(turn, "answer", "")).strip()
        if question:
            messages.append({"role": "user", "content": question})
        if answer:
            messages.append({"role": "assistant", "content": answer})
    return messages[-12:]


def _run_graph(
    *,
    question: str,
    team: str,
    language: str,
    history: Sequence[Any],
    wiki_root: Path,
    provider: ChatProvider,
    topic_label: str = "",
) -> dict[str, Any]:
    resolved_wiki = wiki_root.expanduser().resolve(strict=True)
    runtime = Runtime(
        wiki_root=resolved_wiki,
        output_dir=artifact_dir_for(resolved_wiki),
        provider=provider,
        max_pages=QA_REASONING_MAX_PAGES,
        max_page_chars=WIKI_QA_MAX_PAGE_CHARS,
        max_candidates=QA_REASONING_MAX_CANDIDATES,
    )
    ensure_artifacts(runtime)
    topic = resolve_topic(team, topic_label)
    with bind_runtime(runtime):
        return run_qa_pipeline(
            question=question,
            language=language,
            robot_topic=topic.label,
            active_topic=topic.as_dict(),
            strict_robot_scope=QA_STRICT_ROBOT_SCOPE,
            history=_history_messages(history),
            stream=True,
            defer_final_answer=True,
        )


async def _drain_stream(
    iterator: Iterator[str],
    on_token: Callable[[str], Awaitable[None]],
    *,
    timeout: int,
) -> str:
    sentinel = object()

    def next_token() -> object:
        try:
            return next(iterator)
        except StopIteration:
            return sentinel

    async def drain() -> str:
        chunks: list[str] = []
        while True:
            value = await asyncio.to_thread(next_token)
            if value is sentinel:
                return "".join(chunks)
            chunk = str(value)
            if not chunk:
                continue
            chunks.append(chunk)
            await on_token(chunk)

    # Python 3.10 compatible; asyncio.timeout is only available in Python 3.11.
    return await asyncio.wait_for(drain(), timeout=max(1, int(timeout)))


def _private_image_markers(result: dict[str, Any], wiki_root: Path) -> str:
    image_root = (wiki_root / "media").resolve()
    selected_pages = {
        str(path) for path in result.get("selected_pages", []) if isinstance(path, str)
    }
    permitted_images: dict[str, dict[str, Any]] = {}
    catalog_path = artifact_dir_for(wiki_root) / "image_catalog.jsonl"
    try:
        for line in catalog_path.read_text(encoding="utf-8").splitlines():
            item = json.loads(line)
            if (
                isinstance(item, dict)
                and item.get("source_page") in selected_pages
                and item.get("usefulness") != "low"
                and item.get("image_type") not in {"decorative", "logo"}
            ):
                permitted_images[str(item.get("path", ""))] = item
    except (OSError, json.JSONDecodeError):
        return ""
    markers: list[str] = []
    seen: set[Path] = set()
    for selected in result.get("selected_images", [])[:3]:
        if not isinstance(selected, dict):
            continue
        raw_path = str(selected.get("path", "")).strip().replace("\\", "/")
        if raw_path.startswith("wiki/"):
            raw_path = raw_path.removeprefix("wiki/")
        if raw_path not in permitted_images:
            continue
        relative = Path(raw_path)
        if relative.is_absolute() or ".." in relative.parts:
            continue
        candidate = wiki_root / relative
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(image_root)
        except (FileNotFoundError, OSError, ValueError):
            continue
        if candidate.is_symlink() or not resolved.is_file() or resolved in seen:
            continue
        seen.add(resolved)
        transport_path = "wiki/" + resolved.relative_to(wiki_root).as_posix()
        alt = str(selected.get("supports_claim", "")).replace("]", " ").strip()
        alt = alt[:200] or resolved.stem
        markers.append(f"![{alt}]({quote(transport_path, safe='/-._~')})")
    return "\n".join(markers)


async def stream_answer(
    *,
    question: str,
    team: str,
    language: str,
    history: Sequence[Any],
    wiki_root: Path,
    provider: ChatProvider,
    on_token: Callable[[str], Awaitable[None]],
    topic_label: str = "",
) -> str:
    """Run the ported LangGraph Q&A pipeline and stream only its final answer."""
    result = await asyncio.to_thread(
        _run_graph,
        question=question,
        team=team,
        language=language,
        history=history,
        wiki_root=wiki_root,
        provider=provider,
        topic_label=topic_label,
    )
    system = str(result.get("answer_system", ""))
    user = str(result.get("answer_user", ""))
    if system and user:
        answer = await _drain_stream(
            provider.stream(system, user),
            on_token,
            timeout=getattr(provider, "timeout", 240),
        )
    else:
        answer = str(result.get("answer", "")).strip()
        if answer:
            await on_token(answer)
    if not answer.strip():
        raise RuntimeError("LangGraph Q&A returned an empty answer")

    markers = _private_image_markers(result, wiki_root.resolve())
    log.info(
        "LangGraph Q&A completed team=%s calls=%s rounds=%s elapsed_ms=%.1f images=%d",
        team,
        result.get("llm_call_count", 0),
        result.get("retrieval_round", 0),
        float(result.get("elapsed_ms", 0.0)),
        len(result.get("selected_images", [])),
    )
    return answer.rstrip() + (("\n\n" + markers) if markers else "")
