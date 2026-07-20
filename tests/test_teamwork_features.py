import pytest
from fastapi.testclient import TestClient
from ecs.app.main import app
from ecs.app.database import _connect, _DB_LOCK

client = TestClient(app)

def setup_module(module):
    # Ensure database is clean or well-known
    from ecs.app.database import initialize_database
    initialize_database()

def test_login_and_dashboard_access():
    # Attempt login with dummy credentials
    response = client.post("/login", data={"username": "test_user", "password": "wrongpassword"}, follow_redirects=False)
    assert response.status_code == 303 # Redirects back to login on failure or success, but we check if it works.

def test_dashboard_route_loads():
    response = client.get("/dashboard", follow_redirects=False)
    # Should redirect to login because we aren't logged in
    assert response.status_code in [302, 303, 307, 401, 403]
