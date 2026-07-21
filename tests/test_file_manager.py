from __future__ import annotations

import json

import pytest

from worker import config as worker_config
from worker import file_manager
from worker.file_manager import (
    FileManagerError,
    SourceBusyError,
    soft_delete_robot,
    soft_delete_source,
)


@pytest.mark.parametrize("source_path", ["../escape", "bad team/source.md"])
def test_invalid_team_path_is_reported_as_file_manager_validation(
    source_path: str,
) -> None:
    with pytest.raises(FileManagerError):
        soft_delete_source(source_path)


def _configure_worker_paths(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(worker_config, "WORKER_ROOT_DIR", tmp_path)
    monkeypatch.setattr(
        worker_config,
        "LLM_WIKI_QUEUE_FILE",
        tmp_path / ".llm-wiki" / "ingest-queue.json",
    )
    monkeypatch.setattr(
        worker_config,
        "LLM_WIKI_CACHE_FILE",
        tmp_path / ".llm-wiki" / "ingest-cache.json",
    )
    monkeypatch.setattr(file_manager, "TRASH_DIR", tmp_path / ".agent1-trash")


def test_soft_delete_robot_moves_complete_folder_to_trash(monkeypatch, tmp_path):
    _configure_worker_paths(monkeypatch, tmp_path)
    robot_folder = tmp_path / "raw" / "sources" / "robot_four"
    robot_folder.mkdir(parents=True)
    (robot_folder / "manual.md").write_text("content", encoding="utf-8")

    result = soft_delete_robot("robot_four")

    assert result["removed"] is True
    assert result["deleted_files"] == 1
    assert not robot_folder.exists()
    trashed_folder = tmp_path / result["trash_path"]
    assert (trashed_folder / "manual.md").read_text(encoding="utf-8") == "content"


def test_soft_delete_robot_is_blocked_during_ingestion(monkeypatch, tmp_path):
    _configure_worker_paths(monkeypatch, tmp_path)
    robot_folder = tmp_path / "raw" / "sources" / "busy_robot"
    robot_folder.mkdir(parents=True)
    queue_file = tmp_path / ".llm-wiki" / "ingest-queue.json"
    queue_file.parent.mkdir(parents=True)
    queue_file.write_text(
        json.dumps(
            [
                {
                    "sourcePath": "busy_robot/upload/manual.md",
                    "status": "processing",
                }
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(SourceBusyError):
        soft_delete_robot("busy_robot")

    assert robot_folder.is_dir()


def test_worker_startup_does_not_recreate_removed_robot_folders(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(worker_config, "WORKER_ROOT_DIR", tmp_path)
    monkeypatch.setattr(worker_config, "STAGING_DIR", tmp_path / ".staging")
    monkeypatch.setattr(worker_config, "TRASH_DIR", tmp_path / ".trash")
    monkeypatch.setattr(worker_config, "AUTHORING_DIR", tmp_path / ".authoring")
    monkeypatch.setattr(
        worker_config,
        "LLM_WIKI_QUEUE_FILE",
        tmp_path / ".llm-wiki" / "ingest-queue.json",
    )
    monkeypatch.setattr(
        worker_config,
        "LLM_WIKI_CACHE_FILE",
        tmp_path / ".llm-wiki" / "ingest-cache.json",
    )
    monkeypatch.setattr(worker_config, "ALLOWED_TEAMS", ("removed_robot",))

    worker_config.ensure_directories()

    assert (tmp_path / "raw" / "sources").is_dir()
    assert not (tmp_path / "raw" / "sources" / "removed_robot").exists()
