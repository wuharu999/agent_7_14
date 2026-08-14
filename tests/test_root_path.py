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
from fastapi.testclient import TestClient
from ecs.app.main import app

assert app.root_path == "/v1/faq-platform"
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
