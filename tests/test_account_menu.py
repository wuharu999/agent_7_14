from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest


TEMPLATE_ROOT = Path(__file__).resolve().parents[1] / "ecs" / "app" / "templates"
STATIC_ROOT = Path(__file__).resolve().parents[1] / "ecs" / "app" / "static"
ACCOUNT_MENU_TEMPLATES = (
    "ask.html",
    "manage.html",
    "upload.html",
    "upload_status.html",
    "settings.html",
    "admin_users.html",
)


@pytest.mark.parametrize("template_name", ACCOUNT_MENU_TEMPLATES)
def test_shared_account_settings_menu_is_loaded_on_every_application_page(
    template_name: str,
) -> None:
    page = (TEMPLATE_ROOT / template_name).read_text(encoding="utf-8")

    assert 'href="/static/account_menu.css"' in page
    assert 'src="/static/account_menu.js?v=20260729-2"' in page
    assert "data-account-menu" in page


def test_login_page_does_not_show_an_account_menu() -> None:
    page = (TEMPLATE_ROOT / "login.html").read_text(encoding="utf-8")
    assert "data-account-menu" not in page


def test_login_page_uses_youbida_only_for_the_chinese_browser_title() -> None:
    page = (TEMPLATE_ROOT / "login.html").read_text(encoding="utf-8")
    assert '<title data-i18n="title">优必答登录</title>' in page
    assert 'title: "优必答登录"' in page
    assert '<h1 data-i18n="header">Uchat 登录</h1>' in page
    assert 'header: "Uchat 登录"' in page
    assert 'title: "Uchat Sign In"' in page


def test_account_actions_are_not_duplicated_in_page_templates() -> None:
    for template_name in ACCOUNT_MENU_TEMPLATES:
        page = (TEMPLATE_ROOT / template_name).read_text(encoding="utf-8")
        assert 'href="/admin/users"' not in page
        assert 'href="/logout"' not in page

    page = (TEMPLATE_ROOT / "ask.html").read_text(encoding="utf-8")
    assert 'id="exportWiki"' not in page
    assert 'id="exportWikiBtn"' not in page


def test_application_pages_do_not_link_to_removed_wecom_ask_route() -> None:
    for template_name in ACCOUNT_MENU_TEMPLATES:
        page = (TEMPLATE_ROOT / template_name).read_text(encoding="utf-8")
        assert 'href="/wecom-ask"' not in page


def test_shared_component_contains_role_gated_admin_and_account_actions() -> None:
    script = (STATIC_ROOT / "account_menu.js").read_text(encoding="utf-8")
    assert "if (user.role === 'admin')" in script
    assert "users.href = '/admin/users'" in script
    assert "manage.href = '/manage'" in script
    assert "upload.href = '/upload'" in script
    assert "fetch('/api/export/wiki')" in script
    assert "fetch('/logout'" in script
    assert "accountSettings" in script
    assert "accountSettings.href = '/settings'" in script
    assert "window.location.assign('/settings')" in script
    assert "heading.dataset.accountLabel = 'account'" in script
    assert "heading.href = '/settings'" in script


def test_shared_component_javascript_parses() -> None:
    script = (STATIC_ROOT / "account_menu.js").read_text(encoding="utf-8")
    result = subprocess.run(
        ["node", "-e", f"new Function({json.dumps(script)})"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("template_name", ["ask.html", "settings.html"])
def test_account_menu_javascript_parses(template_name: str) -> None:
    page = (TEMPLATE_ROOT / template_name).read_text(encoding="utf-8")
    scripts = re.findall(r"<script>(.*?)</script>", page, flags=re.DOTALL)
    assert scripts

    for script in scripts:
        rendered_script = script.replace("__ALLOWED_TEAMS__", "[]")
        rendered_script = rendered_script.replace("__ROBOTS__", "[]")
        result = subprocess.run(
            ["node", "-e", f"new Function({json.dumps(rendered_script)})"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, result.stderr
