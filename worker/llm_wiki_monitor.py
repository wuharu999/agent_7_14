from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from worker.config import (
    get_allowed_teams,
    LLM_WIKI_MONITOR_TIMEOUT,
    LLM_WIKI_POLL_SECONDS,
    get_team_config,
)

EventCallback = Callable[[dict[str, Any]], Awaitable[None]]
_LLM_WIKI_DEFAULT_MAX_RETRIES = 3
_monotonic = time.monotonic
_MAX_SNAPSHOT_TASKS = 2_000
_MAX_SNAPSHOT_COMPLETION_RECEIPTS = 2_000
_MAX_SNAPSHOT_FILES_WRITTEN = 50


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _normalize(value: str) -> str:
    normalized = value.replace("\\", "/").strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.strip("/")


def _without_sources_prefix(value: str) -> str:
    normalized = _normalize(value)
    prefix = "raw/sources/"
    return normalized[len(prefix):] if normalized.startswith(prefix) else normalized


def _identity_candidates(team: str, source_identity: str) -> tuple[list[str], set[str]]:
    relative = _without_sources_prefix(source_identity)
    team_prefix = f"{team}/"
    if relative.startswith(team_prefix):
        full_identity = relative
        internal_identity = relative[len(team_prefix):]
    else:
        full_identity = f"{team}/{relative}"
        internal_identity = relative

    cache_candidates = [
        full_identity,
        internal_identity,
        f"raw/sources/{full_identity}",
        f"raw/sources/{internal_identity}",
    ]
    return cache_candidates, set(cache_candidates)


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _retry_values(task: dict[str, Any]) -> tuple[int, int]:
    retry_count = max(0, _integer(task.get("retryCount")))
    raw_max_retries = task.get("maxRetries")
    max_retries = max(
        0,
        _integer(
            raw_max_retries,
            _LLM_WIKI_DEFAULT_MAX_RETRIES,
        ),
    )
    if raw_max_retries is None or raw_max_retries == "":
        max_retries = _LLM_WIKI_DEFAULT_MAX_RETRIES
    return retry_count, max_retries


async def monitor_source(
    *,
    team: str,
    upload_id: str,
    source_identity: str,
    published_at_ms: int,
    emit: EventCallback,
) -> None:
    cache_candidates, queue_candidates = _identity_candidates(team, source_identity)
    last_state = ()
    monitoring_window_started = _monotonic()
    tc = get_team_config(team)

    while True:
        queue_data, cache_data = await asyncio.gather(
            asyncio.to_thread(_read_json, tc.llm_wiki_queue_file),
            asyncio.to_thread(_read_json, tc.llm_wiki_cache_file),
        )
        queue_tasks = queue_data if isinstance(queue_data, list) else []
        cache_entries = cache_data.get("entries", {}) if isinstance(cache_data, dict) else {}

        cache_entry = next(
            (
                cache_entries.get(identity)
                for identity in cache_candidates
                if isinstance(cache_entries.get(identity), dict)
            ),
            None,
        )
        if isinstance(cache_entry, dict) and int(cache_entry.get("timestamp", 0)) >= published_at_ms:
            state = "completed"
            event = {
                "type": "job_progress",
                "upload_id": upload_id,
                "source_identity": source_identity,
                "source_status": state,
                "files_written": list(cache_entry.get("filesWritten") or []),
            }
            state_tuple = (state, 0, 0, 0, "")
        else:
            matching_tasks = [
                item
                for item in queue_tasks
                if isinstance(item, dict)
                and _normalize(str(item.get("sourcePath") or "")) in queue_candidates
            ]
            
            active_queue_count = len(matching_tasks)
            state = "waiting"
            retry_count = 0
            max_retries = 0
            error = ""

            for task in matching_tasks:
                task_status = task.get("status")
                task_error = str(task.get("error") or "")
                task_retries, task_max_retries = _retry_values(task)
                permanently_failed = task_status == "failed" and (
                    task_max_retries <= 0 or task_retries >= task_max_retries
                )
                
                if task_status == "processing":
                    state = "processing"
                    retry_count = task_retries
                    max_retries = task_max_retries
                    error = task_error
                    break
                elif permanently_failed and state not in {
                    "processing",
                    "retrying",
                    "queued",
                }:
                    state = "failed"
                    retry_count = task_retries
                    max_retries = task_max_retries
                    error = task_error or "LLM Wiki ingestion failed"
                elif (
                    (task_status == "failed" and not permanently_failed)
                    or (task_error and task_retries > 0)
                ) and state not in {"processing"}:
                    state = "retrying"
                    retry_count = task_retries
                    max_retries = task_max_retries
                    error = task_error
                elif task_status == "pending" and state not in {"processing", "retrying"}:
                    state = "queued"
                    retry_count = task_retries
                    max_retries = task_max_retries
                    error = task_error
                elif task_status == "failed" and state not in {"processing", "retrying", "queued"}:
                    state = "failed"
                    retry_count = task_retries
                    max_retries = task_max_retries
                    error = task_error or "LLM Wiki ingestion failed"
                elif task_status == "cancelled" and state not in {
                    "processing",
                    "retrying",
                    "queued",
                }:
                    state = "failed"
                    retry_count = task_retries
                    max_retries = task_max_retries
                    error = task_error or "LLM Wiki ingestion was cancelled"

            event = {
                "type": "job_progress",
                "upload_id": upload_id,
                "source_identity": source_identity,
                "source_status": state,
                "retry_count": retry_count,
                "max_retries": max_retries,
                "active_queue_count": active_queue_count,
                "error": error,
            }
            state_tuple = (state, retry_count, max_retries, active_queue_count, error)

        if state_tuple != last_state:
            await emit(event)
            last_state = state_tuple

        if state in {"completed", "failed"}:
            return
        if _monotonic() - monitoring_window_started > LLM_WIKI_MONITOR_TIMEOUT:
            # This limit is an observation/heartbeat interval, not evidence that
            # LLM Wiki failed. Its persistent queue can legitimately remain
            # pending or processing for longer (large documents, provider rate
            # limits, or a paused backlog). Keep following queue/cache truth and
            # resend the current state during the next window so ECS can recover
            # from a missed/stale update.
            monitoring_window_started = _monotonic()
            last_state = ()
        await asyncio.sleep(LLM_WIKI_POLL_SECONDS)


async def monitor_global_queue(
    emit: EventCallback,
    *,
    refresh_event: asyncio.Event | None = None,
) -> None:
    last_snapshot_str = ""
    while True:
        counts = {
            "processing": 0,
            "retrying": 0,
            "queued": 0,
            "failed": 0,
            "total": 0,
        }
        all_tasks: list[dict[str, Any]] = []
        completion_receipts: list[dict[str, Any]] = []
        allowed_teams = get_allowed_teams()
        queue_configs: dict[Path, tuple[str, Any]] = {}
        for team in allowed_teams:
            try:
                tc = get_team_config(team)
            except ValueError:
                continue
            queue_path = tc.llm_wiki_queue_file.resolve(strict=False)
            queue_configs.setdefault(queue_path, (team, tc))

        for fallback_team, tc in queue_configs.values():
            if not tc.llm_wiki_queue_file.exists():
                continue

            queue_data, cache_data = await asyncio.gather(
                asyncio.to_thread(_read_json, tc.llm_wiki_queue_file),
                asyncio.to_thread(_read_json, tc.llm_wiki_cache_file),
            )
            queue_tasks = queue_data if isinstance(queue_data, list) else []
            counts["total"] += len(queue_tasks)

            for original_task in queue_tasks:
                if not isinstance(original_task, dict):
                    continue
                task = dict(original_task)
                original_path = _normalize(str(task.get("sourcePath") or ""))
                relative_path = _without_sources_prefix(original_path)
                first_segment = relative_path.partition("/")[0]
                task_team = (
                    first_segment if first_segment in allowed_teams else fallback_team
                )
                if original_path.startswith("raw/sources/"):
                    display_path = original_path
                elif relative_path.startswith(f"{task_team}/"):
                    display_path = f"raw/sources/{relative_path}"
                else:
                    display_path = f"raw/sources/{task_team}/{relative_path}"
                task["sourcePath"] = display_path
                task["team"] = task_team

                status = task.get("status")
                error = task.get("error")
                retries, max_retries = _retry_values(task)
                permanently_failed = status == "failed" and (
                    max_retries <= 0 or retries >= max_retries
                )
                if status == "processing":
                    task["sourceStatus"] = "processing"
                    counts["processing"] += 1
                elif permanently_failed:
                    task["sourceStatus"] = "failed"
                    counts["failed"] += 1
                elif error and retries > 0:
                    task["sourceStatus"] = "retrying"
                    counts["retrying"] += 1
                elif status == "pending":
                    task["sourceStatus"] = "queued"
                    counts["queued"] += 1
                elif status == "failed":
                    task["sourceStatus"] = "failed"
                    counts["failed"] += 1
                elif status == "cancelled":
                    task["sourceStatus"] = "failed"
                    task["error"] = str(error or "LLM Wiki ingestion was cancelled")
                    counts["failed"] += 1
                    
                all_tasks.append(task)

            cache_entries = (
                cache_data.get("entries", {}) if isinstance(cache_data, dict) else {}
            )
            for source_identity, entry in cache_entries.items():
                if not isinstance(source_identity, str) or not isinstance(entry, dict):
                    continue
                raw_files_written = entry.get("filesWritten")
                completion_receipts.append(
                    {
                        "sourceIdentity": _without_sources_prefix(source_identity),
                        "timestamp": max(0, _integer(entry.get("timestamp"))),
                        "filesWritten": [
                            str(path)
                            for path in (
                                raw_files_written[:_MAX_SNAPSHOT_FILES_WRITTEN]
                                if isinstance(raw_files_written, list)
                                else []
                            )
                        ],
                    }
                )

        completion_receipts.sort(
            key=lambda receipt: (
                -int(receipt["timestamp"]),
                str(receipt["sourceIdentity"]),
            )
        )
        completion_receipts = completion_receipts[
            :_MAX_SNAPSHOT_COMPLETION_RECEIPTS
        ]
                
        snapshot_state = {
            "counts": counts,
            "tasks": all_tasks[:_MAX_SNAPSHOT_TASKS],
            "completion_receipts": completion_receipts,
        }
        
        snapshot_str = json.dumps(snapshot_state, ensure_ascii=False, sort_keys=True)
        refresh_requested = refresh_event is not None and refresh_event.is_set()
        if snapshot_str != last_snapshot_str or refresh_requested:
            snapshot = {
                "type": "llm_wiki_snapshot",
                "generated_at": time.time(),
                **snapshot_state,
            }
            await emit(snapshot)
            last_snapshot_str = snapshot_str
            if refresh_requested:
                refresh_event.clear()
            
        await asyncio.sleep(LLM_WIKI_POLL_SECONDS)
