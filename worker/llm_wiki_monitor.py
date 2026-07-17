from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx

from worker.config import (
    LLM_WIKI_API_TOKEN,
    LLM_WIKI_API_URL,
    LLM_WIKI_CACHE_FILE,
    LLM_WIKI_MONITOR_TIMEOUT,
    LLM_WIKI_POLL_SECONDS,
    LLM_WIKI_PROJECT_ID,
    LLM_WIKI_QUEUE_FILE,
)

EventCallback = Callable[[dict[str, Any]], Awaitable[None]]


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _normalize(value: str) -> str:
    return value.replace("\\", "/").lstrip("./")


async def request_rescan() -> None:
    if not LLM_WIKI_API_TOKEN or not LLM_WIKI_PROJECT_ID:
        return
    url = f"{LLM_WIKI_API_URL}/projects/{LLM_WIKI_PROJECT_ID}/sources/rescan"
    headers = {"Authorization": f"Bearer {LLM_WIKI_API_TOKEN}"}
    try:
        async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
            response = await client.post(url, headers=headers)
            response.raise_for_status()
    except Exception:
        # Auto-watch is still the primary mechanism. Rescan is best effort.
        return


async def monitor_source(
    *,
    upload_id: str,
    source_identity: str,
    published_at_ms: int,
    emit: EventCallback,
) -> None:
    expected_queue_path = f"raw/sources/{_normalize(source_identity)}"
    last_state = ()
    started = time.monotonic()

    while True:
        queue_data, cache_data = await asyncio.gather(
            asyncio.to_thread(_read_json, LLM_WIKI_QUEUE_FILE),
            asyncio.to_thread(_read_json, LLM_WIKI_CACHE_FILE),
        )
        queue_tasks = queue_data if isinstance(queue_data, list) else []
        cache_entries = cache_data.get("entries", {}) if isinstance(cache_data, dict) else {}

        cache_entry = cache_entries.get(source_identity)
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
                if _normalize(str(item.get("sourcePath") or ""))
                in {expected_queue_path, _normalize(source_identity)}
            ]
            
            active_queue_count = len(matching_tasks)
            state = "waiting"
            retry_count = 0
            max_retries = 0
            error = ""

            for task in matching_tasks:
                task_status = task.get("status")
                task_error = str(task.get("error") or "")
                task_retries = int(task.get("retryCount") or 0)
                task_max_retries = int(task.get("maxRetries") or 0)
                
                if task_status == "processing":
                    state = "processing"
                    retry_count = task_retries
                    max_retries = task_max_retries
                    error = task_error
                    break
                elif task_error and task_retries > 0 and state not in {"processing"}:
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
        if time.monotonic() - started > LLM_WIKI_MONITOR_TIMEOUT:
            await emit(
                {
                    "type": "job_progress",
                    "upload_id": upload_id,
                    "source_identity": source_identity,
                    "source_status": "failed",
                    "error": "Timed out waiting for LLM Wiki ingestion completion",
                }
            )
            return
        await asyncio.sleep(LLM_WIKI_POLL_SECONDS)


async def monitor_global_queue(emit: EventCallback) -> None:
    last_snapshot_str = ""
    while True:
        queue_data, cache_data = await asyncio.gather(
            asyncio.to_thread(_read_json, LLM_WIKI_QUEUE_FILE),
            asyncio.to_thread(_read_json, LLM_WIKI_CACHE_FILE),
        )
        queue_tasks = queue_data if isinstance(queue_data, list) else []
        
        counts = {
            "processing": 0,
            "retrying": 0,
            "queued": 0,
            "failed": 0,
            "total": len(queue_tasks),
        }
        
        for task in queue_tasks:
            status = task.get("status")
            error = task.get("error")
            retries = int(task.get("retryCount") or 0)
            if status == "processing":
                counts["processing"] += 1
            elif error and retries > 0:
                counts["retrying"] += 1
            elif status == "pending":
                counts["queued"] += 1
            elif status == "failed":
                counts["failed"] += 1
                
        snapshot = {
            "type": "llm_wiki_snapshot",
            "generated_at": time.time(),
            "counts": counts,
            "tasks": queue_tasks[:100]
        }
        
        snapshot_str = json.dumps(counts, sort_keys=True)
        if snapshot_str != last_snapshot_str:
            await emit(snapshot)
            last_snapshot_str = snapshot_str
            
        await asyncio.sleep(LLM_WIKI_POLL_SECONDS)
