from __future__ import annotations

import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "worker_proxy_env.sh"


def sanitized_environment(**proxy_values: str) -> tuple[dict[str, str], str]:
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        **proxy_values,
    }
    result = subprocess.run(
        [
            "bash",
            "-c",
            f'source "{HELPER}"; sanitize_worker_proxy_env; env',
        ],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    values = dict(
        line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
    )
    return values, result.stderr


def test_socks_all_proxy_is_removed_but_http_proxy_is_preserved() -> None:
    values, warning = sanitized_environment(
        ALL_PROXY="socks://127.0.0.1:7897/",
        HTTP_PROXY="http://127.0.0.1:7897/",
        HTTPS_PROXY="http://127.0.0.1:7897/",
        NO_PROXY="127.0.0.1,localhost",
    )

    assert "ALL_PROXY" not in values
    assert values["HTTP_PROXY"] == "http://127.0.0.1:7897/"
    assert values["HTTPS_PROXY"] == "http://127.0.0.1:7897/"
    assert values["NO_PROXY"] == "127.0.0.1,localhost"
    assert "ALL_PROXY" in warning
    assert "127.0.0.1:7897" not in warning


def test_lowercase_socks_proxy_is_removed_and_http_all_proxy_is_allowed() -> None:
    socks_values, _warning = sanitized_environment(
        all_proxy="socks5://127.0.0.1:7897/"
    )
    http_values, warning = sanitized_environment(
        ALL_PROXY="http://127.0.0.1:7897/"
    )

    assert "all_proxy" not in socks_values
    assert http_values["ALL_PROXY"] == "http://127.0.0.1:7897/"
    assert warning == ""


def test_worker_launcher_always_sanitizes_proxy_before_python() -> None:
    launcher = (ROOT / "scripts" / "run_worker.sh").read_text(encoding="utf-8")

    assert 'source "$ROOT/scripts/worker_proxy_env.sh"' in launcher
    assert launcher.index("sanitize_worker_proxy_env") < launcher.index(
        "exec .venv-worker/bin/python"
    )
