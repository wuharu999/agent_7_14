import asyncio
import io
import pytest
from pathlib import Path
from httpx import AsyncClient, ASGITransport
from fastapi.testclient import TestClient

from ecs.app.main import app
from ecs.app.gateway import gateway
from ecs.app import config, auth, database

@pytest.fixture(autouse=True)
def cleanup_gateway_exports():
    gateway.pending_exports.clear()
    yield
    gateway.pending_exports.clear()


import uuid

def _create_admin_session():
    uid = uuid.uuid4().hex[:6]
    user_id = database.create_user_record(
        username=f"admin_{uid}",
        email=f"admin_{uid}@example.com",
        password_hash="hash",
        password_salt="salt",
        role="admin",
        teams="tian_gong",
    )
    token, _csrf = auth.create_login_session(user_id)
    return token


def test_export_wiki_unauthenticated(monkeypatch):
    client = TestClient(app)
    response = client.get("/api/export/wiki")
    assert response.status_code == 401


def test_export_wiki_offline(monkeypatch):
    monkeypatch.setattr(gateway, "websocket", None)
    token = _create_admin_session()
    client = TestClient(app)
    client.cookies.set(config.SESSION_COOKIE_NAME, token)
    response = client.get("/api/export/wiki")
    assert response.status_code == 503
    assert response.json()["error"] == "Worker is offline"


@pytest.mark.anyio
async def test_export_wiki_success(monkeypatch):
    monkeypatch.setattr(gateway, "websocket", object())
    
    sent_msgs = []
    async def mock_send(msg):
        sent_msgs.append(msg)
    monkeypatch.setattr(gateway, "send", mock_send)
    
    from ecs.app.routes import ask as ask_route
    monkeypatch.setattr(ask_route, "WORKER_SHARED_SECRET", "test_secret")
    
    fake_zip_data = b"PK\x03\x04fake_zip_bytes"
    token = _create_admin_session()
    cookies = {config.SESSION_COOKIE_NAME: token}
    
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies=cookies) as ac:
        get_task = asyncio.create_task(ac.get("/api/export/wiki"))
        
        for _ in range(10):
            await asyncio.sleep(0.05)
            if len(sent_msgs) > 0:
                break
                
        if get_task.done():
            res = get_task.result()
            print("GET response status:", res.status_code)
            print("GET response body:", res.text)
            
        assert len(sent_msgs) == 1
        export_id = sent_msgs[0]["export_id"]
        
        files = {"file": ("wiki_export.zip", io.BytesIO(fake_zip_data), "application/zip")}
        headers = {"X-Worker-Secret": "test_secret"}
        post_response = await ac.post(f"/api/worker/upload-export/{export_id}", files=files, headers=headers)
        assert post_response.status_code == 200
        assert post_response.json() == {"status": "ok"}
        
        get_response = await get_task
        assert get_response.status_code == 200, get_response.text
        assert get_response.content == fake_zip_data
        assert get_response.headers["content-type"] == "application/zip"


@pytest.mark.anyio
async def test_worker_handle_create_export(monkeypatch, tmp_path):
    from worker import manager as w_manager
    from worker import config as w_config
    
    # Create fake wiki files
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    (wiki_dir / "index.md").write_text("# Unified Index", encoding="utf-8")
    concepts_dir = wiki_dir / "concepts"
    concepts_dir.mkdir()
    (concepts_dir / "concept1.md").write_text("# Concept 1", encoding="utf-8")
    
    # Set WORKER_ROOT_DIR and secret on w_manager level to avoid cache issues
    monkeypatch.setattr(w_config, "WORKER_ROOT_DIR", tmp_path)
    monkeypatch.setattr(w_manager, "WORKER_ROOT_DIR", tmp_path)
    monkeypatch.setattr(w_manager, "WORKER_SHARED_SECRET", "test_worker_secret")
    monkeypatch.setattr(w_config, "SERVER_URL", "ws://127.0.0.1:8000/ws/client")
    
    # Mock httpx POST request from Worker to ECS
    uploaded_payloads = []
    class MockResponse:
        def raise_for_status(self):
            pass
            
    async def mock_post(client_self, url, *args, **kwargs):
        headers = kwargs.get("headers")
        files = kwargs.get("files")
        uploaded_payloads.append((url, headers, files["file"][1].read()))
        return MockResponse()
        
    import httpx
    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)
    
    manager = w_manager.WorkerManager()
    await manager.handle_create_export("export-xyz")
    
    # Verify that the zip was successfully uploaded
    assert len(uploaded_payloads) == 1
    url, headers, file_bytes = uploaded_payloads[0]
    assert url == "http://127.0.0.1:8000/api/worker/upload-export/export-xyz"
    assert headers["X-Worker-Secret"] == "test_worker_secret"
    
    # Read the uploaded zip file bytes and verify it contains our wiki files
    import zipfile
    zip_buf = io.BytesIO(file_bytes)
    with zipfile.ZipFile(zip_buf) as z:
        namelist = z.namelist()
        assert "index.md" in namelist
        assert "concepts/concept1.md" in namelist
