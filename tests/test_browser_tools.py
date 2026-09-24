import pytest
from fastapi.testclient import TestClient

from ecs.app import auth, config, database
from ecs.app.main import app
from ecs.app.routes import pages
from ecs.app.routes import auth as auth_routes


@pytest.fixture
def tool_account(tmp_path, monkeypatch):
    monkeypatch.setattr(database, 'DATABASE_PATH', tmp_path / 'accounts.db')
    monkeypatch.setattr(pages, 'BROWSER_TOOLS_URL', 'http://tools.test:8080')
    monkeypatch.setattr(auth_routes, 'BROWSER_TOOLS_URL', 'http://tools.test:8080')
    database.initialize_database()
    return TestClient(app)


@pytest.mark.parametrize('role', ['editor', 'admin'])
def test_tools_share_login_and_preserve_grill_deep_links(tool_account, role):
    client = tool_account
    response = client.get('/tools/grill/s/test-session', follow_redirects=False)
    assert response.status_code == 303
    assert response.headers['location'].endswith('/login?next=/tools/grill/s/test-session')
    uid = auth.create_or_update_user('tools_' + role, role + '@example.test', 'TestPassword2026!', role)
    token, _ = auth.create_login_session(uid)
    client.cookies.set(config.SESSION_COOKIE_NAME, token)
    assert client.get('/api/me').json()['tools_enabled'] is True
    for path in ['log', 'grill', 'grill/s/test-session']:
        response = client.get('/tools/' + path, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers['location'] == 'http://tools.test:8080/' + path
    database.toggle_user_active(uid)
    assert client.get('/tools/log', follow_redirects=False).headers['location'].endswith('/login?next=/tools/log')


def test_tools_redirect_cannot_target_arbitrary_urls(tool_account):
    for path in ['https://evil.test', '/evil.test', 'grill/%2e%2e/admin', 'grill%5cadmin']:
        assert tool_account.get('/tools/' + path, follow_redirects=False).status_code == 404


def test_unconfigured_tools_are_not_advertised(tool_account, monkeypatch):
    uid = auth.create_or_update_user('tools_editor', 'editor@example.test', 'TestPassword2026!', 'editor')
    token, _ = auth.create_login_session(uid)
    tool_account.cookies.set(config.SESSION_COOKIE_NAME, token)
    monkeypatch.setattr(pages, 'BROWSER_TOOLS_URL', '')
    monkeypatch.setattr(auth_routes, 'BROWSER_TOOLS_URL', '')
    assert tool_account.get('/api/me').json()['tools_enabled'] is False
    assert tool_account.get('/tools/log', follow_redirects=False).status_code == 503
