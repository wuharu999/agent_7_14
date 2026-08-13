from __future__ import annotations

from pathlib import Path

import pytest

from ecs.app import database


@pytest.fixture(autouse=True)
def isolated_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "agent_jobs.db")
    database.initialize_database()


def _create_published_upload() -> None:
    database.create_upload(
        upload_id="upload-1",
        task_id="task-1",
        team="walker_s2",
        filename="manual.pdf",
        size_bytes=100,
        ecs_path="/tmp/manual.pdf",
        status="waiting_for_llm_wiki",
        stage="waiting_for_llm_wiki",
        message="waiting",
    )
    database.register_sources(
        "upload-1",
        ["walker_s2/upload-1/manual.pdf"],
        published_at_ms=100,
    )


def test_live_worker_snapshot_repairs_stale_failed_upload_status() -> None:
    _create_published_upload()
    database.upsert_source(
        upload_id="upload-1",
        source_identity="walker_s2/upload-1/manual.pdf",
        status="failed",
        error="old Worker monitoring timeout",
    )

    changed = database.reconcile_llm_wiki_snapshot(
        {
            "tasks": [
                {
                    "sourcePath": "raw/sources/walker_s2/upload-1/manual.pdf",
                    "sourceStatus": "processing",
                    "status": "processing",
                    "retryCount": 0,
                }
            ],
            "completion_receipts": [],
        }
    )

    upload = database.get_upload("upload-1")
    assert changed == 1
    assert upload is not None
    assert upload["status"] == "ingesting"
    assert upload["stage"] == "llm_wiki_ingestion"
    assert upload["sources"][0]["status"] == "processing"
    assert upload["sources"][0]["error"] is None


def test_completion_receipt_finishes_upload_after_worker_restart() -> None:
    _create_published_upload()

    database.reconcile_llm_wiki_snapshot(
        {
            "tasks": [],
            "completion_receipts": [
                {
                    "sourceIdentity": "walker_s2/upload-1/manual.pdf",
                    "timestamp": 101,
                    "filesWritten": ["wiki/sources/manual.md"],
                }
            ],
        }
    )

    upload = database.get_upload("upload-1")
    assert upload is not None
    assert upload["status"] == "completed"
    assert upload["stage"] == "ingestion_completed"
    assert upload["sources"][0]["status"] == "completed"
    assert upload["sources"][0]["files_written"] == ["wiki/sources/manual.md"]


def test_older_completion_receipt_cannot_complete_new_publication() -> None:
    _create_published_upload()

    changed = database.reconcile_llm_wiki_snapshot(
        {
            "tasks": [],
            "completion_receipts": [
                {
                    "sourceIdentity": "walker_s2/upload-1/manual.pdf",
                    "timestamp": 99,
                    "filesWritten": ["wiki/sources/stale.md"],
                }
            ],
        }
    )

    upload = database.get_upload("upload-1")
    assert changed == 0
    assert upload is not None
    assert upload["sources"][0]["status"] == "waiting"


def test_retrying_source_keeps_upload_active() -> None:
    _create_published_upload()
    database.reconcile_llm_wiki_snapshot(
        {
            "tasks": [
                {
                    "sourcePath": "walker_s2/upload-1/manual.pdf",
                    "sourceStatus": "retrying",
                    "status": "pending",
                    "retryCount": 1,
                    "error": "temporary provider limit",
                }
            ],
            "completion_receipts": [],
        }
    )

    upload = database.get_upload("upload-1")
    assert upload is not None
    assert upload["status"] == "ingesting"
    assert upload["sources"][0]["status"] == "retrying"
    assert upload["progress"]["retrying"] == 1


def test_malformed_snapshot_values_are_ignored_safely() -> None:
    _create_published_upload()

    changed = database.reconcile_llm_wiki_snapshot(
        {
            "tasks": [
                {
                    "sourcePath": "walker_s2/upload-1/manual.pdf",
                    "sourceStatus": "queued",
                    "retryCount": "not-a-number",
                    "maxRetries": None,
                }
            ],
            "completion_receipts": [
                {
                    "sourceIdentity": "walker_s2/upload-1/manual.pdf",
                    "timestamp": "invalid",
                    "filesWritten": "not-a-list",
                }
            ],
        }
    )

    upload = database.get_upload("upload-1")
    assert changed == 1
    assert upload is not None
    assert upload["sources"][0]["status"] == "queued"
    assert upload["sources"][0]["retry_count"] == 0
    assert upload["sources"][0]["max_retries"] == 3
