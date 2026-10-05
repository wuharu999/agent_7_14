from __future__ import annotations

import uuid
import pytest
from fastapi.testclient import TestClient

from ecs.app import auth, config, database
from ecs.app.main import app
from worker.langgraph_qa.qa.nodes.final_answer import ANSWER_SYSTEM_PROMPT


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "agent_jobs.db")
    database.initialize_database()


def _user(role: str) -> tuple[int, str, str]:
    suffix = uuid.uuid4().hex[:8]
    user_id = database.create_user_record(
        username=f"{role}_{suffix}",
        email=f"{role}_{suffix}@example.com",
        password_hash="hash",
        password_salt="salt",
        role=role,
        teams="",
    )
    token, csrf = auth.create_login_session(user_id)
    return user_id, token, csrf


def _client_for_role(role: str) -> tuple[TestClient, str]:
    _user_id, token, csrf = _user(role)
    client = TestClient(app)
    client.cookies.set(config.SESSION_COOKIE_NAME, token)
    return client, csrf


def test_chat_robot_options_seeded_and_crud():
    options = database.list_all_chat_robot_options()
    assert len(options) > 0
    # Seeded options contain standard robots
    keys = [opt["name"] for opt in options]
    assert "walker_s2" in keys

    # Create new option decoupled from folder
    opt_id = database.create_chat_robot_option(
        name="custom_bot",
        display_name_zh="定制客服机器人",
        display_name_en="Custom Service Bot",
        folder_name="walker_s2",
        description="A specialized bot",
    )
    assert opt_id > 0
    item = database.get_chat_robot_option_by_name("custom_bot")
    assert item is not None
    assert item["display_name_zh"] == "定制客服机器人"
    assert item["display_name_en"] == "Custom Service Bot"
    assert item["folder_name"] == "walker_s2"
    assert item["is_enabled"] == 1

    # Update option
    updated = database.update_chat_robot_option(
        opt_id,
        display_name_zh="更新后机器人",
        display_name_en="Updated Bot",
        is_enabled=0,
    )
    assert updated["display_name_zh"] == "更新后机器人"
    assert updated["display_name_en"] == "Updated Bot"
    assert updated["is_enabled"] == 0

    # Enabled options should exclude disabled
    active_options = database.get_chat_robot_options()
    active_keys = [opt["name"] for opt in active_options]
    assert "custom_bot" not in active_keys

    # Delete option
    deleted = database.delete_chat_robot_option(opt_id)
    assert deleted is not None
    assert database.get_chat_robot_option_by_id(opt_id) is None


def test_chat_robots_api_editor_and_admin_permissions():
    # 1. Editor can manage chat robots
    ed_client, ed_csrf = _client_for_role("editor")
    resp = ed_client.get("/api/manage/chat_robots")
    assert resp.status_code == 200
    data = resp.json()
    assert "chat_robots" in data
    assert "folders" in data

    # Editor create
    create_resp = ed_client.post(
        "/api/manage/chat_robots",
        json={
            "name": "ed_bot",
            "display_name_zh": "编辑创建机器人",
            "display_name_en": "Editor Created Bot",
            "folder_name": "walker_s2",
            "description": "test",
        },
        headers={"X-CSRF-Token": ed_csrf},
    )
    assert create_resp.status_code == 200
    new_id = create_resp.json()["id"]

    # Editor patch
    patch_resp = ed_client.patch(
        f"/api/manage/chat_robots/{new_id}",
        json={"display_name_zh": "编辑修改名称"},
        headers={"X-CSRF-Token": ed_csrf},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["chat_robot"]["display_name_zh"] == "编辑修改名称"

    # 2. Admin can also manage
    adm_client, adm_csrf = _client_for_role("admin")
    order_resp = adm_client.put(
        "/api/manage/chat_robots/order",
        json={"option_ids": [new_id]},
        headers={"X-CSRF-Token": adm_csrf},
    )
    assert order_resp.status_code == 200

    del_resp = adm_client.delete(
        f"/api/manage/chat_robots/{new_id}",
        headers={"X-CSRF-Token": adm_csrf},
    )
    assert del_resp.status_code == 200

    # 3. Unauthenticated user is rejected
    anon_client = TestClient(app)
    assert anon_client.get("/api/manage/chat_robots").status_code in (401, 303)


def test_chat_dropdown_renders_decoupled_options():
    # Insert custom option with custom key and decoupled label
    database.create_chat_robot_option(
        name="virtual_guide",
        display_name_zh="虚拟客服向导",
        display_name_en="Virtual Guide",
        folder_name="walker_s2",
    )

    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "virtual_guide" in resp.text
    assert "虚拟客服向导" in resp.text
    assert "Virtual Guide" in resp.text


def test_manage_page_folder_management_and_chat_robots_section():
    ed_client, _ed_csrf = _client_for_role("editor")
    resp = ed_client.get("/manage")
    assert resp.status_code == 200
    text = resp.text

    # Header renamed to folder management
    assert "文件夹管理" in text or "Folder Management" in text
    # Chat robots section
    assert 'id="chat-robots"' in text
    assert 'id="chat-robots-table"' in text
    assert 'id="chat-robots-tbody"' in text
    assert 'id="create-chat-robot-form"' in text
    assert "loadChatRobots" in text


def test_final_answer_prompt_robot_availability_rule():
    # Model prompt includes rule linking back to /manage#chat-robots
    assert "[查看或配置可用机器人](/manage#chat-robots)" in ANSWER_SYSTEM_PROMPT
    assert "[View or configure available robots](/manage#chat-robots)" in ANSWER_SYSTEM_PROMPT
