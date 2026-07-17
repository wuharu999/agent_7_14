from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from worker.config import (
    FILE_MANAGER_MAX_ENTRIES,
    SUPPORTED_SOURCE_SUFFIXES,
    TRASH_DIR,
    get_team_config,
    ALLOWED_TEAMS,
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


def _resolve_relative(team: str, relative_path: str, *, allow_root: bool = False) -> tuple[Path, str]:
    raw = (relative_path or "").strip().replace("\\", "/")
    if raw.startswith("/") or Path(raw).is_absolute():
        raise FileManagerError("Absolute paths are not allowed")
    
    tc = get_team_config(team)
    root = tc.raw_sources_dir.resolve()
    
    # Strip the team name from the path if it's the first part, because the old system 
    # included the team name in the relative path (e.g., tian_gong/upload-id/file.md)
    # With the new system, tc.raw_sources_dir already points to the team's folder.
    parts = list(Path(raw).parts)
    if parts and parts[0] == team:
        parts = parts[1:]
    
    normalized = "/".join(parts)
    
    if not normalized:
        if allow_root:
            return root, ""
        raise FileManagerError("The raw/sources root cannot be removed")
        
    if any(part in {"", ".", ".."} for part in Path(normalized).parts):
        raise FileManagerError("Invalid source path")

    candidate = root.joinpath(*Path(normalized).parts)
    parent = candidate.parent.resolve()
    if parent != root and root not in parent.parents:
        raise FileManagerError("Source path escapes raw/sources")
    if candidate.is_symlink():
        raise FileManagerError("Symbolic links cannot be managed")
    resolved = candidate.resolve(strict=False)
    if resolved != root and root not in resolved.parents:
        raise FileManagerError("Source path escapes raw/sources")
    return resolved, normalized


def _ingestion_states(team: str) -> dict[str, dict[str, Any]]:
    tc = get_team_config(team)
    states: dict[str, dict[str, Any]] = {}
    
    cache_data = _read_json(tc.llm_wiki_cache_file, {})
    if isinstance(cache_data, dict):
        entries = cache_data.get("entries", {})
        if isinstance(entries, dict):
            for identity, entry in entries.items():
                states[_normalize(str(identity))] = {
                    "status": "completed",
                    "files_written": list(entry.get("filesWritten") or []) if isinstance(entry, dict) else [],
                }

    queue_data = _read_json(tc.llm_wiki_queue_file, [])
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
    counter = 0

    def build(path: Path, root_dir: Path, states: dict[str, dict[str, Any]]) -> dict[str, Any]:
        nonlocal counter
        counter += 1
        if counter > FILE_MANAGER_MAX_ENTRIES:
            raise FileManagerError(
                f"Source tree exceeds FILE_MANAGER_MAX_ENTRIES={FILE_MANAGER_MAX_ENTRIES}"
            )
        relative = path.relative_to(root_dir).as_posix()
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
            children = [build(child, root_dir, states) for child in sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))]
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

    team_children = []
    for team in ALLOWED_TEAMS:
        tc = get_team_config(team)
        if not tc.raw_sources_dir.exists():
            continue
            
        states = _ingestion_states(team)
        children = [
            build(child, tc.raw_sources_dir, states)
            for child in sorted(tc.raw_sources_dir.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        ]
        
        # In the payload structure, path for files needs to include the team name, since
        # the ECS UI sends commands with `{team}/{path}`
        # Update the paths to include the team prefix
        def add_team_prefix(node: dict[str, Any], team: str) -> None:
            node["path"] = f"{team}/{node['path']}"
            if "children" in node:
                for c in node["children"]:
                    add_team_prefix(c, team)
                    
        for c in children:
            add_team_prefix(c, team)

        team_children.append({
            "name": team,
            "path": team,
            "type": "directory",
            "modified_at": datetime.now(timezone.utc).isoformat(),
            "status": _directory_status(children),
            "children": children,
            "children_count": len(children),
        })

    return {
        "root": "raw/sources",
        "entry_count": counter,
        "children": team_children,
    }


def _processing_sources(team: str) -> list[str]:
    tc = get_team_config(team)
    queue_data = _read_json(tc.llm_wiki_queue_file, [])
    if not isinstance(queue_data, list):
        return []
    return [
        _normalize(str(task.get("sourcePath") or ""))
        for task in queue_data
        if isinstance(task, dict) and task.get("status") == "processing"
    ]


def _assert_not_processing(team: str, normalized_path: str, target: Path) -> None:
    prefix = normalized_path.rstrip("/") + "/"
    for identity in _processing_sources(team):
        if identity == normalized_path or (target.is_dir() and identity.startswith(prefix)):
            raise SourceBusyError(
                f"Source is currently being ingested: {identity}"
            )


def _count_files(path: Path) -> int:
    if path.is_file():
        return 1
    return sum(1 for child in path.rglob("*") if child.is_file() and not child.is_symlink())


def soft_delete_source(relative_path: str) -> dict[str, Any]:
    # ecs paths come in as "{team}/{upload_id}/{file}"
    raw = (relative_path or "").strip().replace("\\", "/")
    parts = Path(raw).parts
    if not parts:
        raise FileManagerError("Invalid source path")
        
    team = parts[0]
    target, normalized = _resolve_relative(team, relative_path)
    if not target.exists():
        raise FileManagerError("Source path does not exist")
        
    _assert_not_processing(team, normalized, target)

    deleted_files = _count_files(target)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    trash_root = TRASH_DIR / f"{stamp}-{uuid4().hex[:8]}"
    destination = trash_root / team / normalized
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.rename(target, destination)

    # Remove empty upload directories, but never team's raw/sources itself.
    parent = target.parent
    tc = get_team_config(team)
    root = tc.raw_sources_dir.resolve()
    while parent != root:
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent

    return {
        "path": f"{team}/{normalized}",
        "trash_path": destination.relative_to(TRASH_DIR.parent).as_posix(),
        "deleted_files": deleted_files,
        "deleted_type": "directory" if destination.is_dir() else "file",
    }
