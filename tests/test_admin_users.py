import json
import re
import subprocess
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
    ["/admin/users", "/manage", "/upload", "/uploads/example-upload", "/settings"],
)
def test_protected_pages_redirect_unauthenticated_users_to_login(page_path: str):
    client = TestClient(app)
    response = client.get(page_path)
    assert response.status_code == 200
    assert response.url.path == "/login"
    assert response.url.params.get("next") == page_path
    assert response.history[0].status_code == 303


def test_admin_users_redirects_editor_to_manage_but_api_stays_forbidden():
    client = TestClient(app)
    _, _, token, _ = _create_user(role="editor")
    client.cookies.set(config.SESSION_COOKIE_NAME, token)
    response = client.get("/admin/users", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/manage"

    api_response = client.get("/api/admin/users")
    assert api_response.status_code == 403


@pytest.mark.parametrize(
    ("role", "expected_destination"),
    [("editor", "/manage"), ("admin", "/admin/users")],
)
def test_login_destination_respects_role(role: str, expected_destination: str):
    client = TestClient(app)
    suffix = uuid.uuid4().hex[:8]
    username = f"login_{suffix}"
    password = "SecureLoginPassword123!"
    auth.create_or_update_user(
        username=username,
        email=f"{username}@example.com",
        password=password,
        role=role,
        teams="tian_gong" if role == "editor" else "",
    )

    response = client.post(
        "/login",
        data={
            "username": username,
            "password": password,
            "next_url": "/admin/users",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == expected_destination


def test_admin_users_page_success():
    client = TestClient(app)
    _, _, token, _ = _create_user(role="admin")
    client.cookies.set(config.SESSION_COOKIE_NAME, token)
    response = client.get("/admin/users")
    assert response.status_code == 200
    assert "用户与文件夹权限管理" in response.text
    assert "removeDisabledUser" in response.text
    assert "删除账户" in response.text


def test_username_normalization_supports_chinese_characters():
    assert auth.normalize_username(" 张三_01 ") == "张三_01"


def test_user_creation_accepts_chinese_username_and_keeps_numeric_id():
    user_id = auth.create_or_update_user(
        username="王小明",
        email="wang@example.com",
        password="SecurePassword123!",
        role="editor",
        teams="tian_gong",
    )

    user = database.get_user_by_id(user_id)
    assert user is not None
    assert user["id"] == user_id
    assert user["username"] == "王小明"


def test_user_can_update_own_email_and_password_from_settings():
    client = TestClient(app)
    username = f"settings_{uuid.uuid4().hex[:8]}"
    old_password = "CurrentPassword123!"
    new_password = "ReplacementPassword123!"
    user_id = auth.create_or_update_user(
        username=username,
        email=f"{username}@example.com",
        password=old_password,
        role="editor",
        teams="tian_gong",
    )
    token, csrf = auth.create_login_session(user_id)
    client.cookies.set(config.SESSION_COOKIE_NAME, token)

    response = client.post(
        "/api/settings",
        json={
            "email": "updated-settings@example.com",
            "current_password": old_password,
            "new_password": new_password,
        },
        headers={"X-CSRF-Token": csrf},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "email": "updated-settings@example.com"}
    user = database.get_user_by_id(user_id)
    assert user is not None
    assert user["email"] == "updated-settings@example.com"
    assert auth.authenticate(username, new_password) is not None
    assert auth.authenticate(username, old_password) is None


def test_admin_user_creation_recovers_a_dropped_success_response():
    client = TestClient(app)
    _, _, token, _ = _create_user(role="admin")
    client.cookies.set(config.SESSION_COOKIE_NAME, token)

    page = client.get("/admin/users").text

    assert "responseWasDropped" in page
    assert "系统已从用户列表核实" in page
    assert "submitButton.disabled = true" in page
    scripts = re.findall(r"<script>(.*?)</script>", page, flags=re.DOTALL)
    assert scripts
    for script in scripts:
        result = subprocess.run(
            ["node", "-e", f"new Function({json.dumps(script)})"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, result.stderr


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

    delete_res = client.delete(
        f"/api/admin/users/{created_id}",
        headers={"X-CSRF-Token": csrf},
    )
    assert delete_res.status_code == 200
    assert database.get_user_by_id(created_id) is None


def test_delete_user_requires_an_inactive_account():
    client = TestClient(app)
    _admin_id, _, token, csrf = _create_user(role="admin")
    client.cookies.set(config.SESSION_COOKIE_NAME, token)
    editor_id, _, _, _ = _create_user(role="editor")

    active_delete = client.delete(
        f"/api/admin/users/{editor_id}",
        headers={"X-CSRF-Token": csrf},
    )
    assert active_delete.status_code == 400
    assert active_delete.json()["error"] == "Disable the account before removing it"

    database.toggle_user_active(editor_id)
    deleted = client.delete(
        f"/api/admin/users/{editor_id}",
        headers={"X-CSRF-Token": csrf},
    )
    assert deleted.status_code == 200
