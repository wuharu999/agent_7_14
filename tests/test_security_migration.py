from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ecs.app.database as database
from ecs.app.security_warnings import validated_security_warnings


class SecurityMigrationTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
