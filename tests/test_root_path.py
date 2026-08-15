from __future__ import annotations

import os
import subprocess
import sys

import pytest

from ecs.app.config import normalize_root_path


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("", ""),
        ("/", ""),
        ("v1/faq-platform", "/v1/faq-platform"),
        ("/v1/faq-platform/", "/v1/faq-platform"),
    ],
)
def test_root_path_normalization(configured: str, expected: str) -> None:
    assert normalize_root_path(configured) == expected


def test_configured_root_path_serves_prefixed_and_unprefixed_routes() -> None:
    script = """
import re
from fastapi.testclient import TestClient
from ecs.app.main import app
from ecs.app.web_paths import render_template, rooted_path

assert app.root_path == "/v1/faq-platform"
assert rooted_path("/login") == "/v1/faq-platform/login"
assert rooted_path("/v1/faq-platform/login") == "/v1/faq-platform/login"
assert rooted_path("https://example.com/login") == "https://example.com/login"
client = TestClient(app)
for path in (
    "/v1/faq-platform/",
    "/v1/faq-platform/login",
    "/v1/faq-platform/health",
    "/v1/faq-platform/static/account_menu.js",
    "/",
    "/login",
    "/health",
    "/static/account_menu.js",
):
    response = client.get(path)
    assert response.status_code == 200, (path, response.status_code)

login = client.get("/v1/faq-platform/login")
assert 'action="/v1/faq-platform/login"' in login.text

ask = client.get("/v1/faq-platform/")
assert 'href="/v1/faq-platform/static/account_menu.css"' in ask.text
assert 'src="/v1/faq-platform/static/account_menu.js' in ask.text
assert 'const appRoot = "/v1/faq-platform"' in ask.text

protected = client.get("/v1/faq-platform/manage", follow_redirects=False)
assert protected.status_code == 303
assert protected.headers["location"] == "/v1/faq-platform/login?next=/manage"

invalid_login = client.post(
    "/v1/faq-platform/login",
    data={"username": "missing", "password": "wrong", "next": "/manage"},
    follow_redirects=False,
)
assert invalid_login.status_code == 303
assert invalid_login.headers["location"].startswith(
    "/v1/faq-platform/login?error=1&next=/manage"
)

attribute_pattern = re.compile(
    r'\\b(?:href|src|action|data-account-return)\\s*=\\s*["\\\'](?P<path>/(?!/)[^"\\\']*)'
)
for name in (
    "admin_users.html",
    "ask.html",
    "login.html",
    "manage.html",
    "settings.html",
    "upload.html",
    "upload_status.html",
):
    rendered = render_template(name)
    for match in attribute_pattern.finditer(rendered):
        assert match.group("path").startswith("/v1/faq-platform/"), (
            name,
            match.group("path"),
        )
"""
    environment = os.environ.copy()
    environment["ROOT_PATH"] = "/v1/faq-platform"
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=os.getcwd(),
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
