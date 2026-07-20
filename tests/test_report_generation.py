from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from ecs.app.main import app
from ecs.app.database import _connect, _DB_LOCK

client = TestClient(app)

def setup_module(module):
    from ecs.app.database import initialize_database
    initialize_database()

def test_generate_report_requires_auth():
    # Calling POST without any CSRF or active session should fail or redirect
    response = client.post("/api/manage/generate_report", json={})
    assert response.status_code in [302, 303, 307, 401, 403, 405]

def test_generate_report_with_mock_auth(monkeypatch):
    # Mock require_roles to bypass session validation for testing
    from ecs.app.routes import manage
    
    # Mock require_roles to return dummy user info
    monkeypatch.setattr(manage, "require_roles", lambda req, roles: {"user_id": 1, "username": "admin", "role": "admin"})
    monkeypatch.setattr(manage, "verify_csrf", lambda session, token: None)
    
    # Insert dummy QA visitor and audit log records to verify analysis
    with _DB_LOCK, _connect() as conn:
        conn.execute("DELETE FROM qa_visitors")
        conn.execute("DELETE FROM file_audit_log")
        conn.execute(
            "INSERT INTO qa_visitors (ip_address, user_id, visited_at) VALUES (?, ?, ?)",
            ("127.0.0.1", 1, "2026-07-20T12:00:00")
        )
        conn.execute(
            "INSERT INTO file_audit_log (user_id, username, action, source_path, result, details, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (1, "admin", "test_action", "test_path", "success", "no details", "2026-07-20T12:05:00")
        )
    
    response = client.post(
        "/api/manage/generate_report",
        headers={"X-CSRF-Token": "mock_csrf"}
    )
    assert response.status_code == 200
    data = response.json()
    assert "report" in data
    report = data["report"]
    assert "# User Activity & Geolocation Report" in report
    assert "127.0.0.1" in report
    assert "test_action" in report
    assert "test_path" in report
