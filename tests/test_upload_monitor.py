from __future__ import annotations

import asyncio

import pytest

from worker import manager as worker_manager


@pytest.mark.anyio
async def test_publish_and_monitor_relies_on_source_watch_without_rescan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = worker_manager.WorkerManager()
    emitted: list[dict[str, object]] = []
    monitored: list[dict[str, object]] = []

    async def fake_emit(message: dict[str, object]) -> None:
        emitted.append(message)

    async def fake_monitor_source(**kwargs: object) -> None:
        monitored.append(kwargs)

    monkeypatch.setattr(manager, "emit", fake_emit)
    monkeypatch.setattr(worker_manager, "monitor_source", fake_monitor_source)

    await manager.publish_and_monitor(
        upload_id="upload-123",
        team="walker_s2",
        identities=["walker_s2/upload-123/manual.pdf"],
        published_at_ms=123456,
    )
    tasks = list(manager.monitor_tasks)
    assert tasks
    await asyncio.gather(*tasks)

    assert monitored == [
        {
            "team": "walker_s2",
            "upload_id": "upload-123",
            "source_identity": "walker_s2/upload-123/manual.pdf",
            "published_at_ms": 123456,
            "emit": fake_emit,
        }
    ]
    assert emitted == [
        {
            "type": "sources_published",
            "upload_id": "upload-123",
            "source_identities": ["walker_s2/upload-123/manual.pdf"],
            "published_at_ms": 123456,
        }
    ]
