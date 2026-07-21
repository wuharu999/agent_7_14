from __future__ import annotations

import html
import json
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ecs.app.auth import current_session, safe_next_url
from ecs.app.database import get_allowed_teams
from shared.source_types import SUPPORTED_UPLOAD_SUFFIXES, UPLOAD_ACCEPT

router = APIRouter()
_TEMPLATE_ROOT = Path(__file__).resolve().parents[1] / "templates"


def _template(name: str) -> str:
    content = (_TEMPLATE_ROOT / name).read_text(encoding="utf-8")
    if name != "bg_graph.html" and "</body>" in content:
        bg = (_TEMPLATE_ROOT / "bg_graph.html").read_text(encoding="utf-8")
        content = content.replace("</body>", bg + "\n</body>")
    return content


def _login_redirect(next_url: str) -> RedirectResponse:
    return RedirectResponse(f"/login?next={quote(next_url, safe='/')}", status_code=303)


@router.get("/", response_class=HTMLResponse)
async def ask_page():
    page = _template("ask.html")
    page = page.replace("__ALLOWED_TEAMS__", json.dumps(get_allowed_teams(), ensure_ascii=False))
    return HTMLResponse(page)


@router.get("/wecom-ask", response_class=HTMLResponse)
async def wecom_ask_page():
    page = _template("wecom_ask.html")
    page = page.replace("__ALLOWED_TEAMS__", json.dumps(get_allowed_teams(), ensure_ascii=False))
    return HTMLResponse(page)



@router.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    error: int = Query(default=0),
    next_url: str = Query(default="/manage", alias="next"),
):
    if current_session(request) is not None:
        return RedirectResponse(safe_next_url(next_url, "/manage"), status_code=303)
    page = _template("login.html")
    page = page.replace("__NEXT_URL__", html.escape(safe_next_url(next_url, "/manage"), quote=True))
    page = page.replace("__LOGIN_ERROR_CODE__", str(error))
    return HTMLResponse(page)


@router.get("/upload", response_class=HTMLResponse)
async def upload_page(request: Request):
    session = current_session(request)
    if session is None:
        return _login_redirect("/upload")
    if session["role"] not in {"editor", "admin"}:
        return HTMLResponse("Upload permission required", status_code=403)
    page = _template("upload.html")
    page = page.replace("__ALLOWED_TEAMS__", json.dumps(get_allowed_teams(), ensure_ascii=False))
    page = page.replace("__UPLOAD_ACCEPT__", html.escape(UPLOAD_ACCEPT, quote=True))
    page = page.replace(
        "__SUPPORTED_UPLOAD_SUFFIXES__",
        json.dumps(sorted(SUPPORTED_UPLOAD_SUFFIXES)),
    )
    page = page.replace("__CSRF_TOKEN__", html.escape(str(session["csrf_token"]), quote=True))
    page = page.replace("__USERNAME__", html.escape(str(session["username"])))
    page = page.replace("__ROLE__", html.escape(str(session["role"])))
    return HTMLResponse(page)


@router.get("/manage", response_class=HTMLResponse)
async def manage_page(request: Request):
    session = current_session(request)
    if session is None:
        return _login_redirect("/manage")
    page = _template("manage.html")
    page = page.replace("__CSRF_TOKEN__", html.escape(str(session["csrf_token"]), quote=True))
    page = page.replace("__USERNAME__", html.escape(str(session["username"])))
    page = page.replace("__ROLE__", html.escape(str(session["role"])))
    page = page.replace(
        "__CAN_DELETE__",
        "true" if session["role"] in {"editor", "admin"} else "false",
    )
    return HTMLResponse(page)


@router.get("/uploads/{upload_id}", response_class=HTMLResponse)
async def upload_status_page(upload_id: str, request: Request):
    if current_session(request) is None:
        return _login_redirect(f"/uploads/{upload_id}")
    return HTMLResponse(_template("upload_status.html").replace("__UPLOAD_ID__", html.escape(upload_id)))
