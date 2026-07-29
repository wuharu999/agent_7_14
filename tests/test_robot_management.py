from __future__ import annotations

import json
import subprocess
import uuid

import pytest
from fastapi.testclient import TestClient

from ecs.app import auth, config, database
from ecs.app.main import app
from ecs.app.routes import manage


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "agent_jobs.db")
    database.initialize_database()


def _user(role: str, *, active: bool = True) -> tuple[int, str, str]:
    suffix = uuid.uuid4().hex[:8]
    user_id = database.create_user_record(
        username=f"{role}_{suffix}",
        email=f"{role}_{suffix}@example.com",
        password_hash="hash",
        password_salt="salt",
        role=role,
        teams="",
    )
    if not active:
        database.toggle_user_active(user_id)
    token, csrf = auth.create_login_session(user_id)
    return user_id, token, csrf


def _admin_client() -> tuple[TestClient, str]:
    _user_id, token, csrf = _user("admin")
    client = TestClient(app)
    client.cookies.set(config.SESSION_COOKIE_NAME, token)
    return client, csrf


def test_upload_page_renders_all_account_placeholders():
    client, _csrf = _admin_client()

    response = client.get("/upload")

    assert response.status_code == 200
    assert "__USERNAME__" not in response.text
    assert "__ROLE__" not in response.text
    assert '<span id="user-role">admin</span>' in response.text


def test_removed_wecom_ask_page_returns_not_found():
    client, _csrf = _admin_client()

    assert client.get("/wecom-ask").status_code == 404


def test_create_robot_waits_for_worker_then_updates_chat_and_folder(
    monkeypatch, tmp_path
):
    client, csrf = _admin_client()
    robot_name = "robot_new_1"
    robot_folder = tmp_path / "raw" / "sources" / robot_name
    calls = []

    monkeypatch.setattr(manage.gateway, "websocket", object())

    async def create_folder(message_type: str, **payload):
        calls.append((message_type, payload))
        robot_folder.mkdir(parents=True)
        return {"status": "ok", "team": payload["team"]}

    monkeypatch.setattr(manage.gateway, "command", create_folder)

    response = client.post(
        "/api/manage/robots",
        json={"name": robot_name, "chinese_name": "新机器人", "description": "New robot"},
        headers={"X-CSRF-Token": csrf},
    )

    assert response.status_code == 200
    assert calls == [("create_robot_folder", {"team": robot_name})]
    assert robot_folder.is_dir()
    assert database.get_robot_by_name(robot_name) is not None
    assert robot_name in client.get("/").text
    assert robot_name in client.get("/upload").text


def test_create_robot_offline_does_not_persist(monkeypatch):
    client, csrf = _admin_client()
    monkeypatch.setattr(manage.gateway, "websocket", None)

    response = client.post(
        "/api/manage/robots",
        json={"name": "offline_robot", "chinese_name": "离线机器人"},
        headers={"X-CSRF-Token": csrf},
    )

    assert response.status_code == 503
    assert database.get_robot_by_name("offline_robot") is None


def test_admin_can_update_robot_display_names_used_by_chat_selector():
    client, csrf = _admin_client()
    robot_id = database.create_robot(
        "display_robot",
        display_name_en="Old English",
        display_name_zh="旧中文",
    )

    response = client.patch(
        f"/api/manage/robots/{robot_id}",
        json={"english_name": "Walker Display", "chinese_name": "行者展示"},
        headers={"X-CSRF-Token": csrf},
    )

    assert response.status_code == 200
    robot = database.get_robot_by_id(robot_id)
    assert robot is not None
    assert robot["name"] == "display_robot"
    assert robot["display_name_en"] == "Walker Display"
    assert robot["display_name_zh"] == "行者展示"
    chat_page = client.get("/").text
    assert '"english_name": "Walker Display"' in chat_page
    assert '"chinese_name": "行者展示"' in chat_page


def test_old_database_keeps_all_configured_robots_after_upgrade(monkeypatch):
    with database._DB_LOCK, database._connect() as connection:
        connection.execute("DELETE FROM robot_editors")
        connection.execute("DELETE FROM robots")
        connection.execute("UPDATE users SET teams = ''")

    custom_robot_id = database.create_robot("debug_robot_api_714")
    monkeypatch.setattr(
        database,
        "ALLOWED_TEAMS",
        ("tian_gong", "walker_s2", "walker_c1"),
    )

    database.initialize_database()
    robots = database.get_all_robots()

    assert {robot["name"] for robot in robots} == {
        "tian_gong",
        "walker_s2",
        "walker_c1",
        "debug_robot_api_714",
    }
    assert database.get_robot_by_name("debug_robot_api_714")["id"] == custom_robot_id
    assert database.get_allowed_teams() == [
        "tian_gong",
        "walker_s2",
        "walker_c1",
        "debug_robot_api_714",
    ]


def test_refresh_source_tree_is_authoritative_for_robot_list(monkeypatch):
    client, _csrf = _admin_client()
    editor_id, _token, _editor_csrf = _user("editor")
    stale_robot_id = database.create_robot("hahabot")
    database.assign_robot_editor(stale_robot_id, editor_id)
    with database._DB_LOCK, database._connect() as connection:
        connection.execute(
            "INSERT INTO robots (name, description, storage_path, created_at) "
            "VALUES (?, '', ?, ?)",
            ("桀桀桀", "桀桀桀", database.utc_now()),
        )

    source_robots = ["tian_gong", "walker_c1", "walker_s2", "walker_s3"]

    async def list_tree(message_type: str, **payload):
        assert message_type == "list_sources"
        assert payload == {}
        return {
            "status": "ok",
            "tree": {
                "root": "raw/sources",
                "entry_count": 4,
                "children": [
                    {
                        "name": name,
                        "path": name,
                        "type": "directory",
                        "children": [],
                    }
                    for name in source_robots
                ],
            },
        }

    monkeypatch.setattr(manage.gateway, "command", list_tree)
    response = client.get("/api/manage/sources")

    assert response.status_code == 200
    assert [item["name"] for item in response.json()["tree"]["children"]] == source_robots
    assert [robot["name"] for robot in database.get_all_robots()] == source_robots
    assert database.get_user_by_id(editor_id)["teams"] == ""
    assert database.robots_source_tree_synced() is True
    assert database.get_allowed_teams() == source_robots

    monkeypatch.setattr(
        database,
        "ALLOWED_TEAMS",
        ("tian_gong", "walker_s2", "walker_c1", "configured_but_missing"),
    )
    database.initialize_database()
    assert [robot["name"] for robot in database.get_all_robots()] == source_robots
    assert database.get_allowed_teams() == source_robots


def test_admin_remove_robot_waits_for_worker_soft_delete(monkeypatch):
    client, csrf = _admin_client()
    editor_id, _token, _editor_csrf = _user("editor")
    robot_id = database.create_robot("remove_me")
    database.assign_robot_editor(robot_id, editor_id)
    calls = []

    async def delete_folder(message_type: str, **payload):
        calls.append((message_type, payload))
        return {
            "status": "ok",
            "path": "remove_me",
            "trash_path": ".agent1-trash/backup/remove_me",
            "deleted_files": 3,
            "removed": True,
        }

    monkeypatch.setattr(manage.gateway, "command", delete_folder)
    response = client.delete(
        f"/api/manage/robots/{robot_id}",
        headers={"X-CSRF-Token": csrf},
    )

    assert response.status_code == 200
    assert calls == [("delete_robot_folder", {"team": "remove_me"})]
    assert database.get_robot_by_id(robot_id) is None
    assert database.get_user_by_id(editor_id)["teams"] == ""
    assert response.json()["trash_path"] == ".agent1-trash/backup/remove_me"
    assert database.list_audit_log(1)[0]["action"] == "delete_robot"


def test_admin_remove_robot_keeps_metadata_when_worker_is_busy(monkeypatch):
    client, csrf = _admin_client()
    robot_id = database.create_robot("busy_robot")

    async def busy_folder(message_type: str, **payload):
        return {
            "status": "busy",
            "error": "Robot has a source currently being ingested",
        }

    monkeypatch.setattr(manage.gateway, "command", busy_folder)
    response = client.delete(
        f"/api/manage/robots/{robot_id}",
        headers={"X-CSRF-Token": csrf},
    )

    assert response.status_code == 409
    assert database.get_robot_by_id(robot_id) is not None


@pytest.mark.parametrize("name", ["../escape", "/absolute", "all", "default", "has space"])
def test_create_robot_rejects_unsafe_or_reserved_names(monkeypatch, name):
    client, csrf = _admin_client()
    monkeypatch.setattr(manage.gateway, "websocket", object())

    response = client.post(
        "/api/manage/robots",
        json={"name": name, "chinese_name": "测试机器人"},
        headers={"X-CSRF-Token": csrf},
    )

    assert response.status_code == 400


def test_editor_pool_drag_assignment_and_removal():
    client, csrf = _admin_client()
    editor_id, _editor_token, _editor_csrf = _user("editor")
    inactive_editor_id, _token, _csrf = _user("editor", active=False)
    other_admin_id, _admin_token, _admin_csrf = _user("admin")
    robot_id = database.create_robot("assignment_robot")

    pool = client.get("/api/manage/editors")
    assert pool.status_code == 200
    pool_ids = {editor["id"] for editor in pool.json()["editors"]}
    assert editor_id in pool_ids
    assert inactive_editor_id not in pool_ids
    assert other_admin_id not in pool_ids

    assigned = client.post(
        f"/api/manage/robots/{robot_id}/editors",
        json={"user_id": editor_id},
        headers={"X-CSRF-Token": csrf},
    )
    assert assigned.status_code == 200
    assert database.get_user_by_id(editor_id)["teams"] == "assignment_robot"
    assert [item["id"] for item in database.get_robot_editors(robot_id)] == [editor_id]

    rejected_admin = client.post(
        f"/api/manage/robots/{robot_id}/editors",
        json={"user_id": other_admin_id},
        headers={"X-CSRF-Token": csrf},
    )
    assert rejected_admin.status_code == 400

    removed = client.delete(
        f"/api/manage/robots/{robot_id}/editors/{editor_id}",
        headers={"X-CSRF-Token": csrf},
    )
    assert removed.status_code == 200
    assert database.get_user_by_id(editor_id)["teams"] == ""
    assert database.get_robot_editors(robot_id) == []

    page = client.get("/manage").text
    assert 'id="editor-pool"' in page
    assert "dataTransfer.setData('text/plain'" in page
    assert "removeButton.textContent = '×'" in page
    assert "/api/manage/robots/${robot.id}" in page
    assert "editRobotNames" in page
    assert "method: 'PATCH'" in page
    scripts = []
    cursor = 0
    while True:
        start = page.find("<script>", cursor)
        if start < 0:
            break
        end = page.find("</script>", start)
        assert end > start
        scripts.append(page[start + len("<script>") : end])
        cursor = end + len("</script>")
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


@pytest.mark.anyio
async def test_worker_create_robot_folder_and_reject_traversal(monkeypatch, tmp_path):
    from worker import config as worker_config
    from worker.manager import WorkerManager

    monkeypatch.setattr(worker_config, "WORKER_ROOT_DIR", tmp_path)
    manager = WorkerManager()

    await manager.route_message(
        {"type": "create_robot_folder", "id": "safe", "team": "safe_robot"}
    )
    success = manager.outgoing.get_nowait()
    assert success["status"] == "ok"
    assert (tmp_path / "raw" / "sources" / "safe_robot").is_dir()

    await manager.route_message(
        {"type": "create_robot_folder", "id": "unsafe", "team": "../escape"}
    )
    failure = manager.outgoing.get_nowait()
    assert failure["status"] == "failed"
    assert not (tmp_path.parent / "escape").exists()
