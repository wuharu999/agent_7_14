from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette.requests import Request

import ecs.app.database as database
from ecs.app.main import app

client = TestClient(app)


def test_question_report_requires_admin_authentication() -> None:
    response = client.post("/api/manage/question_report", json={})
    assert response.status_code in [302, 303, 307, 401, 403, 405]


def test_ask_records_only_a_validated_question() -> None:
    from ecs.app.routes import ask

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/ask",
            "headers": [],
            "client": ("198.51.100.12", 12345),
            "scheme": "http",
            "server": ("testserver", 80),
        }
    )
    with TemporaryDirectory() as directory:
        database_path = Path(directory) / "ask.db"
        with patch.object(database, "DATABASE_PATH", database_path):
            database.initialize_database()
            response = asyncio.run(
                ask.ask(
                    request,
                    {
                        "question": "What is the current payload limit?",
                        "team": "walker_s2",
                        "language": "en",
                        "conversation_id": "web:accepted-question",
                    },
                )
            )
            assert response.status_code == 200
            invalid_response = asyncio.run(
                ask.ask(
                    request,
                    {
                        "question": "",
                        "team": "walker_s2",
                        "language": "en",
                        "conversation_id": "web:rejected-question",
                    },
                )
            )
            assert invalid_response.status_code == 400
            with database._DB_LOCK, database._connect() as connection:
                records = connection.execute(
                    "SELECT ip_address, conversation_id, team, language, question FROM qa_question_records"
                ).fetchall()

    assert [tuple(record) for record in records] == [
        (
            "198.51.100.12",
            "web:accepted-question",
            "walker_s2",
            "en",
            "What is the current payload limit?",
        )
    ]


def test_question_report_groups_accepted_questions_and_renews_records(
    monkeypatch,
) -> None:
    from ecs.app.routes import manage

    monkeypatch.setattr(
        manage,
        "require_roles",
        lambda request, roles: {"user_id": 1, "username": "admin", "role": "admin"},
    )
    monkeypatch.setattr(manage, "verify_csrf", lambda session, token: None)

    with TemporaryDirectory() as directory:
        database_path = Path(directory) / "questions.db"
        with patch.object(database, "DATABASE_PATH", database_path):
            database.initialize_database()
            now = datetime.now(timezone.utc)
            with database._DB_LOCK, database._connect() as connection:
                connection.executemany(
                    """
                    INSERT INTO qa_question_records (
                        ip_address, conversation_id, team, topic_label, language,
                        question, asked_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            "203.0.113.10",
                            "web:conversation-a",
                            "walker_s2",
                            "Walker S2",
                            "en",
                            "What payload can it carry?",
                            (now - timedelta(minutes=2)).isoformat(),
                        ),
                        (
                            "203.0.113.10",
                            "web:conversation-a",
                            "walker_s2",
                            "Walker S2",
                            "zh-CN",
                            "|和换行\n都需要安全显示",
                            (now - timedelta(minutes=1)).isoformat(),
                        ),
                        (
                            "198.51.100.7",
                            "web:conversation-b",
                            "all",
                            "全部机器人",
                            "zh-CN",
                            "有哪些机器人？",
                            now.isoformat(),
                        ),
                        (
                            "192.0.2.2",
                            "web:expired",
                            "tian_gong",
                            "天工",
                            "en",
                            "This record must expire.",
                            (now - timedelta(days=15)).isoformat(),
                        ),
                    ],
                )

            response = client.post(
                "/api/manage/question_report",
                headers={"X-CSRF-Token": "test-csrf"},
            )
            assert response.status_code == 200
            report = response.json()["report"]
            assert "# 最近 14 天问答记录报告" in report
            assert "**问题总数**：3" in report
            assert "web:conversation-a" in report
            assert "203.0.113.10" in report
            assert "Walker S2" in report
            assert "What payload can it carry?" in report
            assert "\\|和换行<br>都需要安全显示" in report
            assert "This record must expire." not in report
            assert "地理位置" not in report

            with database._DB_LOCK, database._connect() as connection:
                remaining = connection.execute(
                    "SELECT COUNT(*) FROM qa_question_records"
                ).fetchone()[0]
            assert remaining == 3


def test_question_report_migration_preserves_legacy_visitor_rows() -> None:
    with TemporaryDirectory() as directory:
        database_path = Path(directory) / "legacy.db"
        with sqlite3.connect(database_path) as connection:
            connection.execute(
                """
                CREATE TABLE qa_visitors (
                    id INTEGER PRIMARY KEY,
                    ip_address TEXT NOT NULL,
                    user_id INTEGER,
                    visited_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "INSERT INTO qa_visitors VALUES (1, '198.51.100.9', NULL, 'old')"
            )

        with patch.object(database, "DATABASE_PATH", database_path):
            database.initialize_database()
            with sqlite3.connect(database_path) as connection:
                visitor = connection.execute(
                    "SELECT ip_address FROM qa_visitors WHERE id = 1"
                ).fetchone()
                question_table = connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'qa_question_records'"
                ).fetchone()

        assert visitor == ("198.51.100.9",)
        assert question_table == ("qa_question_records",)


def test_question_report_button_uses_the_admin_endpoint() -> None:
    template = (
        Path(__file__).resolve().parents[1] / "ecs" / "app" / "templates" / "manage.html"
    ).read_text(encoding="utf-8")

    assert "Generate question report" in template
    assert "Question records (last 14 days)" in template
    assert "if (role === 'admin')" in template
    assert "fetch('/api/manage/question_report'" in template
    assert "/api/manage/generate_report" not in template
