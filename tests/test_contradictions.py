import uuid
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from ecs.app.main import app
from ecs.app import config, auth, database

def _create_user(role="admin", teams="tian_gong,walker_s2"):
    uid = uuid.uuid4().hex[:6]
    user_id = database.create_user_record(
        username=f"user_{uid}",
        email=f"user_{uid}@example.com",
        password_hash="hash",
        password_salt="salt",
        role=role,
        teams=teams,
    )
    token, csrf = auth.create_login_session(user_id)
    return user_id, f"user_{uid}", token, csrf

def test_wiki_contradictions():
    database.initialize_database()

    # Clean up table first
    with database._DB_LOCK, database._connect() as conn:
        conn.execute("DELETE FROM wiki_contradictions")
        conn.execute("DELETE FROM robots")
        conn.execute("DELETE FROM robot_editors")
        conn.execute("DELETE FROM users")

        # Insert test robots
        conn.execute("INSERT INTO robots (name, description, storage_path, created_at) VALUES ('walker_s2', '', 'walker_s2', '2026-07-21T00:00:00')")
        conn.execute("INSERT INTO robots (name, description, storage_path, created_at) VALUES ('tian_gong', '', 'tian_gong', '2026-07-21T00:00:00')")
        conn.execute("INSERT INTO robots (name, description, storage_path, created_at) VALUES ('walker_c1', '', 'walker_c1', '2026-07-21T00:00:00')")

    # Add old and new contradictions
    database.add_wiki_contradiction("walker_s2", "Conflict details 1")

    # Manually insert a contradiction older than 7 days
    with database._DB_LOCK, database._connect() as conn:
        old_time = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        conn.execute(
            "INSERT INTO wiki_contradictions (team, details, created_at) VALUES (?, ?, ?)",
            ("tian_gong", "Old conflict details", old_time)
        )

    # Test database retrieval
    recent = database.get_recent_wiki_contradictions(days=7)
    assert len(recent) == 1
    assert recent[0]["team"] == "walker_s2"
    assert recent[0]["details"] == "Conflict details 1"

    # Create admin user
    admin_id, _, token_admin, csrf_admin = _create_user(role="admin")

    # Create editor user and grant editor access to walker_s2 but not tian_gong
    editor_id, _, token_editor, csrf_editor = _create_user(role="editor", teams="walker_s2")

    # Test API for Admin (should see recent conflicts)
    client = TestClient(app)
    client.cookies.set(config.SESSION_COOKIE_NAME, token_admin)
    res = client.get("/api/manage/contradictions")
    assert res.status_code == 200
    data = res.json()
    assert "contradictions" in data
    assert len(data["contradictions"]) == 1
    assert data["contradictions"][0]["team"] == "walker_s2"

    # Test API for Editor (who is editor of walker_s2)
    client_editor = TestClient(app)
    client_editor.cookies.set(config.SESSION_COOKIE_NAME, token_editor)
    res_editor = client_editor.get("/api/manage/contradictions")
    assert res_editor.status_code == 200
    data_editor = res_editor.json()
    assert len(data_editor["contradictions"]) == 1

    # Insert a conflict for walker_c1 (which editor doesn't have access to)
    database.add_wiki_contradiction("walker_c1", "Conflict details for c1")

    # Admin should see both walker_s2 and walker_c1
    res_admin2 = client.get("/api/manage/contradictions")
    assert len(res_admin2.json()["contradictions"]) == 2

    # Editor should only see walker_s2
    res_editor2 = client_editor.get("/api/manage/contradictions")
    assert len(res_editor2.json()["contradictions"]) == 1
    assert res_editor2.json()["contradictions"][0]["team"] == "walker_s2"


def test_dynamic_robot_creation():
    database.initialize_database()

    # Get current allowed teams
    initial_teams = database.get_allowed_teams()

    # Create new robot in DB
    new_robot_name = f"robot_{uuid.uuid4().hex[:6]}"
    database.create_robot(name=new_robot_name, description="Test Robot Description", storage_path=new_robot_name)

    # It should now be in the allowed teams list
    updated_teams = database.get_allowed_teams()
    assert new_robot_name in updated_teams
    assert len(updated_teams) == len(initial_teams) + 1
