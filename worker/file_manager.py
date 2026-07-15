from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from worker.config import (
    FILE_MANAGER_MAX_ENTRIES,
    LLM_WIKI_CACHE_FILE,
    LLM_WIKI_QUEUE_FILE,
    RAW_SOURCES_DIR,
    SUPPORTED_SOURCE_SUFFIXES,
    TRASH_DIR,
)


class FileManagerError(RuntimeError):
    pass


class SourceBusyError(FileManagerError):
    pass


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _normalize(value: str) -> str:
    normalized = value.replace("\\", "/").strip().lstrip("./")
    marker = "raw/sources/"
    lower = normalized.lower()
    marker_index = lower.find(marker)
    if marker_index >= 0:
        normalized = normalized[marker_index + len(marker):]
    return normalized.strip("/")


def _resolve_relative(relative_path: str, *, allow_root: bool = False) -> tuple[Path, str]:
    raw = (relative_path or "").strip().replace("\\", "/")
    if raw.startswith("/") or Path(raw).is_absolute():
        raise FileManagerError("Absolute paths are not allowed")
    normalized = _normalize(raw)
    if not normalized:
        if allow_root:
            return RAW_SOURCES_DIR.resolve(), ""
        raise FileManagerError("The raw/sources root cannot be removed")
    if any(part in {"", ".", ".."} for part in Path(normalized).parts):
        raise FileManagerError("Invalid source path")

    root = RAW_SOURCES_DIR.resolve()
    candidate = root.joinpath(*Path(normalized).parts)
    # Resolve the parent first so a malicious symlink cannot escape raw/sources.
    parent = candidate.parent.resolve()
    if parent != root and root not in parent.parents:
        raise FileManagerError("Source path escapes raw/sources")
    if candidate.is_symlink():
        raise FileManagerError("Symbolic links cannot be managed")
    resolved = candidate.resolve(strict=False)
    if resolved != root and root not in resolved.parents:
        raise FileManagerError("Source path escapes raw/sources")
    return resolved, normalized


def _ingestion_states() -> dict[str, dict[str, Any]]:
    states: dict[str, dict[str, Any]] = {}
    cache_data = _read_json(LLM_WIKI_CACHE_FILE, {})
    if isinstance(cache_data, dict):
        entries = cache_data.get("entries", {})
        if isinstance(entries, dict):
            for identity, entry in entries.items():
                states[_normalize(str(identity))] = {
                    "status": "completed",
                    "files_written": list(entry.get("filesWritten") or []) if isinstance(entry, dict) else [],
                }

    queue_data = _read_json(LLM_WIKI_QUEUE_FILE, [])
    if isinstance(queue_data, list):
        for task in queue_data:
            if not isinstance(task, dict):
                continue
            identity = _normalize(str(task.get("sourcePath") or ""))
            if not identity:
                continue
            status = str(task.get("status") or "pending")
            states[identity] = {
                "status": {
                    "pending": "queued",
                    "processing": "processing",
                    "failed": "failed",
                    "done": "completed",
                }.get(status, status),
                "error": task.get("error"),
            }
    return states


def _directory_status(children: list[dict[str, Any]]) -> str:
    statuses: set[str] = set()
    for child in children:
        status = str(child.get("status") or "")
        if status:
            statuses.add(status)
    if "processing" in statuses:
        return "processing"
    if "queued" in statuses:
        return "queued"
    if "failed" in statuses:
        return "partially_failed" if len(statuses) > 1 else "failed"
    if statuses and statuses <= {"completed"}:
        return "completed"
    return ""


def list_source_tree() -> dict[str, Any]:
    RAW_SOURCES_DIR.mkdir(parents=True, exist_ok=True)
    states = _ingestion_states()
    counter = 0

    def build(path: Path) -> dict[str, Any]:
        nonlocal counter
        counter += 1
        if counter > FILE_MANAGER_MAX_ENTRIES:
            raise FileManagerError(
                f"Source tree exceeds FILE_MANAGER_MAX_ENTRIES={FILE_MANAGER_MAX_ENTRIES}"
            )
        relative = path.relative_to(RAW_SOURCES_DIR).as_posix()
        stat = path.lstat()
        modified_at = datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()

        if path.is_symlink():
            return {
                "name": path.name,
                "path": relative,
                "type": "symlink",
                "size_bytes": 0,
                "modified_at": modified_at,
                "status": "blocked",
            }

        if path.is_dir():
            children = [build(child) for child in sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))]
            return {
                "name": path.name,
                "path": relative,
                "type": "directory",
                "modified_at": modified_at,
                "status": _directory_status(children),
                "children": children,
                "children_count": len(children),
            }

        identity = _normalize(relative)
        state = states.get(identity, {})
        return {
            "name": path.name,
            "path": relative,
            "type": "file",
            "size_bytes": stat.st_size,
            "modified_at": modified_at,
            "status": state.get("status", "untracked"),
            "error": state.get("error"),
            "supported": path.suffix.lower() in SUPPORTED_SOURCE_SUFFIXES,
        }

    children = [
        build(child)
        for child in sorted(RAW_SOURCES_DIR.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    ]
    return {
        "root": "raw/sources",
        "entry_count": counter,
        "children": children,
    }


def _processing_sources() -> list[str]:
    queue_data = _read_json(LLM_WIKI_QUEUE_FILE, [])
    if not isinstance(queue_data, list):
        return []
    return [
        _normalize(str(task.get("sourcePath") or ""))
        for task in queue_data
        if isinstance(task, dict) and task.get("status") == "processing"
    ]


def _assert_not_processing(normalized_path: str, target: Path) -> None:
    prefix = normalized_path.rstrip("/") + "/"
    for identity in _processing_sources():
        if identity == normalized_path or (target.is_dir() and identity.startswith(prefix)):
            raise SourceBusyError(
                f"Source is currently being ingested: {identity}"
            )


def _count_files(path: Path) -> int:
    if path.is_file():
        return 1
    return sum(1 for child in path.rglob("*") if child.is_file() and not child.is_symlink())


def soft_delete_source(relative_path: str) -> dict[str, Any]:
    target, normalized = _resolve_relative(relative_path)
    if not target.exists():
        raise FileManagerError("Source path does not exist")
    _assert_not_processing(normalized, target)

    deleted_files = _count_files(target)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    trash_root = TRASH_DIR / f"{stamp}-{uuid4().hex[:8]}"
    destination = trash_root / normalized
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.rename(target, destination)

    # Remove empty upload/team directories, but never raw/sources itself.
    parent = target.parent
    root = RAW_SOURCES_DIR.resolve()
    while parent != root:
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent

    return {
        "path": normalized,
        "trash_path": destination.relative_to(TRASH_DIR.parent).as_posix(),
        "deleted_files": deleted_files,
        "deleted_type": "directory" if destination.is_dir() else "file",
    }
