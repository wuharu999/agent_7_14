from __future__ import annotations

import hashlib
import json
import logging
import shutil
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Protocol
from uuid import uuid4

from worker.langgraph_qa.wiki.indexer import build_catalog_and_index


log = logging.getLogger(__name__)
_INDEX_LOCK = threading.Lock()


class ChatProvider(Protocol):
    timeout: int

    def complete(self, system: str, user: str) -> str: ...

    def stream(self, system: str, user: str) -> Iterator[str]: ...


@dataclass(frozen=True)
class Runtime:
    wiki_root: Path
    output_dir: Path
    provider: ChatProvider
    max_pages: int
    max_page_chars: int
    max_candidates: int

    @property
    def search_db(self) -> Path:
        return self.output_dir / "search.db"

    @property
    def wiki_catalog(self) -> Path:
        return self.output_dir / "wiki_catalog.jsonl"

    @property
    def image_catalog(self) -> Path:
        return self.output_dir / "image_catalog.jsonl"

    @property
    def related_graph(self) -> Path:
        return self.output_dir / "related_graph.json"


_CURRENT_RUNTIME: ContextVar[Runtime | None] = ContextVar(
    "agent1_langgraph_qa_runtime", default=None
)


def artifact_dir_for(wiki_root: Path) -> Path:
    resolved = wiki_root.expanduser().resolve()
    key = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:16]
    return resolved.parent / ".agent1-worker" / "qa-langgraph" / key


def get_runtime() -> Runtime:
    runtime = _CURRENT_RUNTIME.get()
    if runtime is None:
        raise RuntimeError("LangGraph Q&A runtime is not bound")
    return runtime


@contextmanager
def bind_runtime(runtime: Runtime):
    token = _CURRENT_RUNTIME.set(runtime)
    try:
        yield runtime
    finally:
        _CURRENT_RUNTIME.reset(token)


def _wiki_signature(wiki_root: Path) -> str:
    digest = hashlib.sha256()
    for page in sorted(wiki_root.rglob("*.md")):
        if page.is_symlink() or not page.is_file():
            continue
        try:
            relative = page.resolve(strict=True).relative_to(wiki_root)
            stat = page.stat()
        except (FileNotFoundError, OSError, ValueError):
            continue
        digest.update(
            f"{relative.as_posix()}\0{stat.st_mtime_ns}\0{stat.st_size}\n".encode()
        )
    return digest.hexdigest()


def ensure_artifacts(runtime: Runtime) -> None:
    wiki_root = runtime.wiki_root
    if not wiki_root.is_dir():
        raise FileNotFoundError(f"Wiki root is unavailable: {wiki_root}")
    signature = _wiki_signature(wiki_root)
    manifest = runtime.output_dir / "manifest.json"
    required = (
        runtime.search_db,
        runtime.wiki_catalog,
        runtime.image_catalog,
        runtime.related_graph,
        runtime.output_dir / "WIKI_GUIDE.md",
    )

    with _INDEX_LOCK:
        try:
            previous = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            previous = {}
        if previous.get("signature") == signature and all(path.is_file() for path in required):
            return

        runtime.output_dir.parent.mkdir(parents=True, exist_ok=True)
        temporary = runtime.output_dir.parent / f".{runtime.output_dir.name}.tmp-{uuid4().hex}"
        try:
            build_catalog_and_index(wiki_root, temporary)
            runtime.output_dir.mkdir(parents=True, exist_ok=True)
            for source in temporary.iterdir():
                source.replace(runtime.output_dir / source.name)
            pending_manifest = manifest.with_suffix(f".tmp-{uuid4().hex}")
            pending_manifest.write_text(
                json.dumps({"signature": signature}, ensure_ascii=False),
                encoding="utf-8",
            )
            pending_manifest.replace(manifest)
        finally:
            shutil.rmtree(temporary, ignore_errors=True)
        log.info("Rebuilt LangGraph Q&A artifacts for %s", wiki_root)
