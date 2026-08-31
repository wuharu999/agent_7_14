from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ecs.app.database as database
from ecs.app.security_warnings import validated_security_warnings


class SecurityMigrationTests(unittest.TestCase):
    def test_retired_scenario_schema_is_not_created_or_migrated(self) -> None:
        retired_tables = {
            "scenario_assessments",
            "scenario_sessions",
            "scenario_state_versions",
            "scenario_events",
            "scenario_analysis_jobs",
            "scenario_report_revisions",
            "scenario_share_links",
        }
        retired_indexes = {
            "idx_scenario_assessments_created",
            "idx_scenario_assessments_model",
            "idx_scenario_sessions_owner",
            "idx_scenario_events_session_sequence",
            "idx_scenario_jobs_session_status",
            "idx_scenario_reports_session_ordinal",
            "idx_scenario_sessions_pending_reanalysis",
        }

        with tempfile.TemporaryDirectory() as directory:
            fresh_path = Path(directory) / "fresh.db"
            with patch.object(database, "DATABASE_PATH", fresh_path):
                database.initialize_database()
            with sqlite3.connect(fresh_path) as connection:
                objects = {
                    str(row[0])
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type IN ('table', 'index')"
                    )
                }
            self.assertTrue(retired_tables.isdisjoint(objects))
            self.assertTrue(retired_indexes.isdisjoint(objects))

            old_path = Path(directory) / "old-scenario.db"
            with sqlite3.connect(old_path) as connection:
                for table in sorted(retired_tables):
                    connection.execute(
                        f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, marker TEXT NOT NULL)"
                    )
                    connection.execute(
                        f"INSERT INTO {table} (id, marker) VALUES (1, 'preserve-me')"
                    )
                for index in sorted(retired_indexes):
                    connection.execute(
                        f"CREATE INDEX {index} ON scenario_assessments(marker)"
                    )
                connection.commit()
                old_objects = {
                    (str(row[0]), str(row[1]), str(row[2]))
                    for row in connection.execute(
                        "SELECT name, type, sql FROM sqlite_master "
                        "WHERE type IN ('table', 'index') AND name NOT LIKE 'sqlite_%'"
                    )
                    if str(row[0]) in retired_tables | retired_indexes
                }

            with patch.object(database, "DATABASE_PATH", old_path):
                database.initialize_database()
                database.initialize_database()
            with sqlite3.connect(old_path) as connection:
                for table in sorted(retired_tables):
                    row = connection.execute(
                        f"SELECT id, marker FROM {table}"
                    ).fetchone()
                    self.assertEqual(row, (1, "preserve-me"))
                objects_after = {
                    (str(row[0]), str(row[1]), str(row[2]))
                    for row in connection.execute(
                        "SELECT name, type, sql FROM sqlite_master "
                        "WHERE type IN ('table', 'index') AND name NOT LIKE 'sqlite_%'"
                    )
                    if str(row[0]) in retired_tables | retired_indexes
                }
            self.assertEqual(old_objects, objects_after)

    def test_old_database_migrates_additively_and_reruns_safely(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "old.db"
            connection = sqlite3.connect(path)
            connection.execute(
                """
                CREATE TABLE uploads (
                    upload_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL UNIQUE,
                    team TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    ecs_path TEXT NOT NULL,
                    status TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    message TEXT NOT NULL DEFAULT '',
                    percent INTEGER,
                    error TEXT,
                    published_at_ms INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "INSERT INTO uploads "
                "(upload_id, task_id, team, filename, size_bytes, ecs_path, "
                "status, stage, created_at, updated_at) "
                "VALUES ('old', 'task-old', 'tian_gong', 'old.md', 1, '/tmp/old', "
                "'completed', 'ingestion_completed', 'now', 'now')"
            )
            connection.commit()
            connection.close()

            with patch.object(database, "DATABASE_PATH", path):
                database.initialize_database()
                database.initialize_database()
                old_upload = database.get_upload("old")
                database.create_upload(
                    upload_id="new",
                    task_id="task-new",
                    team="tian_gong",
                    filename="new.md",
                    size_bytes=2,
                    ecs_path="/tmp/new",
                    status="waiting_for_worker",
                    stage="uploaded_to_ecs",
                    message="saved",
                )
                new_upload = database.get_upload("new")
                database.replace_upload_security_warnings(
                    "new",
                    [
                        {
                            "source_identity": "tian_gong/new/new.md",
                            "categories": ["instruction_override"],
                        }
                    ],
                    complete=True,
                )
                scanned_upload = database.get_upload("new")

            self.assertIsNotNone(old_upload)
            self.assertTrue(old_upload["security_scan_complete"])
            self.assertIsNotNone(new_upload)
            self.assertFalse(new_upload["security_scan_complete"])
            self.assertTrue(scanned_upload["security_scan_complete"])
            self.assertEqual(
                scanned_upload["security_warnings"][0]["source_identity"],
                "tian_gong/new/new.md",
            )

    def test_worker_warning_payload_rejects_paths_and_unknown_categories(self) -> None:
        warnings = validated_security_warnings(
            [
                {
                    "source_identity": "tian_gong/upload/guide.md",
                    "categories": ["instruction_override", "made_up"],
                    "excerpt": "must not cross the boundary",
                },
                {
                    "source_identity": "tian_gong/upload/guide.md",
                    "categories": ["prompt_exfiltration"],
                },
                {
                    "source_identity": "../outside.md",
                    "categories": ["prompt_exfiltration"],
                },
                {
                    "source_identity": "/absolute.md",
                    "categories": ["prompt_exfiltration"],
                },
            ]
        )
        self.assertEqual(
            warnings,
            [
                {
                    "source_identity": "tian_gong/upload/guide.md",
                    "categories": ["instruction_override", "prompt_exfiltration"],
                }
            ],
        )
        self.assertNotIn("excerpt", warnings[0])

    def test_get_all_upload_timestamps(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.db"
            with patch.object(database, "DATABASE_PATH", path):
                database.initialize_database()
                database.create_upload(
                    upload_id="upload-1",
                    task_id="task-1",
                    team="tian_gong",
                    filename="1.md",
                    size_bytes=10,
                    ecs_path="/tmp/1",
                    status="waiting_for_worker",
                    stage="uploaded_to_ecs",
                    message="saved",
                )
                timestamps = database.get_all_upload_timestamps()
                self.assertIn("upload-1", timestamps)
                self.assertIsInstance(timestamps["upload-1"], str)


if __name__ == "__main__":
    unittest.main()
