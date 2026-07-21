from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from worker import llm_wiki_monitor


@pytest.mark.anyio
async def test_monitor_source_matches_team_in_shared_queue_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    queue_file = tmp_path / "ingest-queue.json"
    cache_file = tmp_path / "ingest-cache.json"
    queue_file.write_text(
        json.dumps(
            [
                {
                    "sourcePath": "raw/sources/walker_s2/upload-1/manual.pdf",
                    "status": "failed",
                    "error": "provider failed",
                    "retryCount": 3,
                }
            ]
        ),
        encoding="utf-8",
    )
    cache_file.write_text("{}", encoding="utf-8")
    config = SimpleNamespace(
        llm_wiki_queue_file=queue_file,
        llm_wiki_cache_file=cache_file,
    )
    monkeypatch.setattr(llm_wiki_monitor, "get_team_config", lambda _team: config)

    events: list[dict[str, object]] = []

    async def emit(event: dict[str, object]) -> None:
        events.append(event)

    await llm_wiki_monitor.monitor_source(
        team="walker_s2",
        upload_id="upload-1",
        source_identity="walker_s2/upload-1/manual.pdf",
        published_at_ms=1,
        emit=emit,
    )

    assert events == [
        {
            "type": "job_progress",
            "upload_id": "upload-1",
            "source_identity": "walker_s2/upload-1/manual.pdf",
            "source_status": "failed",
            "retry_count": 3,
            "max_retries": 3,
            "active_queue_count": 1,
            "error": "provider failed",
        }
    ]


@pytest.mark.anyio
async def test_global_snapshot_counts_shared_queue_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    queue_file = tmp_path / "ingest-queue.json"
    cache_file = tmp_path / "ingest-cache.json"
    queue_file.write_text(
        json.dumps(
            [
                {
                    "sourcePath": "raw/sources/walker_s2/upload-1/manual.pdf",
                    "status": "processing",
                },
                {
                    "sourcePath": "raw/sources/tian_gong/upload-2/guide.md",
                    "status": "pending",
                },
            ]
        ),
        encoding="utf-8",
    )
    cache_file.write_text("{}", encoding="utf-8")
    config = SimpleNamespace(
        llm_wiki_queue_file=queue_file,
        llm_wiki_cache_file=cache_file,
    )
    monkeypatch.setattr(
        llm_wiki_monitor,
        "get_allowed_teams",
        lambda: ["tian_gong", "walker_s2", "walker_c1", "hahabot"],
    )
    monkeypatch.setattr(llm_wiki_monitor, "get_team_config", lambda _team: config)

    class StopMonitor(Exception):
        pass

    async def stop_after_snapshot(_seconds: float) -> None:
        raise StopMonitor

    events: list[dict[str, object]] = []

    async def emit(event: dict[str, object]) -> None:
        events.append(event)

    monkeypatch.setattr(llm_wiki_monitor.asyncio, "sleep", stop_after_snapshot)
    with pytest.raises(StopMonitor):
        await llm_wiki_monitor.monitor_global_queue(emit)

    assert len(events) == 1
    snapshot = events[0]
    assert snapshot["counts"] == {
        "processing": 1,
        "retrying": 0,
        "queued": 1,
        "failed": 0,
        "total": 2,
    }
    tasks = snapshot["tasks"]
    assert isinstance(tasks, list)
    assert len(tasks) == 2
    assert {task["team"] for task in tasks} == {"walker_s2", "tian_gong"}


@pytest.mark.anyio
async def test_global_snapshot_is_resent_when_connection_requests_refresh(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    queue_file = tmp_path / "ingest-queue.json"
    cache_file = tmp_path / "ingest-cache.json"
    queue_file.write_text("[]", encoding="utf-8")
    cache_file.write_text("{}", encoding="utf-8")
    config = SimpleNamespace(
        llm_wiki_queue_file=queue_file,
        llm_wiki_cache_file=cache_file,
    )
    monkeypatch.setattr(llm_wiki_monitor, "get_allowed_teams", lambda: ["walker_s2"])
    monkeypatch.setattr(llm_wiki_monitor, "get_team_config", lambda _team: config)

    class StopMonitor(Exception):
        pass

    refresh_event = llm_wiki_monitor.asyncio.Event()
    sleep_calls = 0

    async def request_refresh_then_stop(_seconds: float) -> None:
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls == 1:
            refresh_event.set()
            return
        raise StopMonitor

    events: list[dict[str, object]] = []

    async def emit(event: dict[str, object]) -> None:
        events.append(event)

    monkeypatch.setattr(
        llm_wiki_monitor.asyncio,
        "sleep",
        request_refresh_then_stop,
    )
    with pytest.raises(StopMonitor):
        await llm_wiki_monitor.monitor_global_queue(
            emit,
            refresh_event=refresh_event,
        )

    assert len(events) == 2
    assert not refresh_event.is_set()
