import uuid
import pytest
from fastapi.testclient import TestClient

from ecs.app.main import app
from ecs.app import config, auth, database


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    database_path = tmp_path / "agent_jobs.db"
    monkeypatch.setattr(database, "DATABASE_PATH", database_path)
    database.initialize_database()


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


@pytest.mark.parametrize(
    "page_path",
    ["/admin/users", "/manage", "/upload", "/uploads/example-upload"],
)
def test_protected_pages_redirect_unauthenticated_users_to_login(page_path: str):
    client = TestClient(app)
    response = client.get(page_path)
    assert response.status_code == 200
    assert response.url.path == "/login"
    assert response.url.params.get("next") == page_path
    assert response.history[0].status_code == 303


def test_admin_users_forbidden_for_editor():
    client = TestClient(app)
    _, _, token, _ = _create_user(role="editor")
    client.cookies.set(config.SESSION_COOKIE_NAME, token)
    response = client.get("/admin/users")
    assert response.status_code == 403


def test_admin_users_page_success():
    client = TestClient(app)
    _, _, token, _ = _create_user(role="admin")
    client.cookies.set(config.SESSION_COOKIE_NAME, token)
    response = client.get("/admin/users")
    assert response.status_code == 200
    assert "用户与文件夹权限管理" in response.text


def test_api_create_and_update_user():
    client = TestClient(app)
    admin_id, _, token, csrf = _create_user(role="admin")
    client.cookies.set(config.SESSION_COOKIE_NAME, token)

    # List users
    res = client.get("/api/admin/users")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"

    # Create new user
    new_name = f"new_{uuid.uuid4().hex[:4]}"
    create_payload = {
        "username": new_name,
        "email": f"{new_name}@example.com",
        "password": "SecurePassword123!",
        "role": "editor",
        "teams": ["tian_gong"],
    }
    create_res = client.post(
        "/api/admin/users/create",
        json=create_payload,
        headers={"X-CSRF-Token": csrf},
    )
    assert create_res.status_code == 200
    created_id = create_res.json()["user_id"]

    # Verify user created with tian_gong
    user_data = database.get_user_by_id(created_id)
    assert user_data["username"] == new_name
    assert user_data["teams"] == "tian_gong"

    # Update user folder access to tian_gong and walker_s2
    update_payload = {
        "role": "editor",
        "teams": ["tian_gong", "walker_s2"],
        "password": "NewSecurePassword123!",
    }
    update_res = client.post(
        f"/api/admin/users/{created_id}/update",
        json=update_payload,
        headers={"X-CSRF-Token": csrf},
    )
    assert update_res.status_code == 200

    # Verify updated teams
    user_updated = database.get_user_by_id(created_id)
    assert "walker_s2" in user_updated["teams"]

    # Toggle active
    toggle_res = client.post(
        f"/api/admin/users/{created_id}/toggle_active",
        headers={"X-CSRF-Token": csrf},
    )
    assert toggle_res.status_code == 200
    assert toggle_res.json()["is_active"] is False
