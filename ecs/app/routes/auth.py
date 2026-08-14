from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from urllib.parse import quote

from fastapi import APIRouter, Form, Header, Request
from fastapi.responses import RedirectResponse, JSONResponse
from pydantic import BaseModel, Field


from ecs.app.auth import (
    authenticate,
    clear_session_cookie,
    create_login_session,
    current_session,
    hash_password,
    normalize_email,
    logout_session,
    safe_next_url,
    safe_next_url_for_role,
    set_session_cookie,
    verify_csrf,
    verify_password,
)
from ecs.app.database import get_user_by_id, update_user_email, update_user_password, write_audit
from ecs.app.web_paths import rooted_path

router = APIRouter()
_LOGIN_WINDOW_SECONDS = 15 * 60
_LOGIN_MAX_FAILURES = 5
_login_failures: dict[str, list[float]] = defaultdict(list)


class UserSettingsRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    current_password: str = Field(min_length=10, max_length=256)
    new_password: str | None = Field(default=None, min_length=10, max_length=256)


@router.get("/api/me")
async def get_me(request: Request):
    session = current_session(request)
    if session is None:
        return JSONResponse({"logged_in": False, "role": None, "username": None})
    return JSONResponse({
        "logged_in": True,
        "user_id": session.get("user_id"),
        "username": session.get("username"),
        "email": session.get("email"),
        "role": session.get("role"),
        "csrf_token": session.get("csrf_token"),
    })


@router.post("/api/settings")
async def update_settings(
    payload: UserSettingsRequest,
    request: Request,
    x_csrf_token: str = Header(default="", alias="X-CSRF-Token"),
):
    session = current_session(request)
    if session is None:
        return JSONResponse({"error": "Login required"}, status_code=401)
    verify_csrf(session, x_csrf_token)
    user = get_user_by_id(int(session["user_id"]))
    if user is None or not verify_password(
        payload.current_password, str(user["password_hash"]), str(user["password_salt"])
    ):
        return JSONResponse({"error": "Current password is incorrect"}, status_code=400)
    try:
        email = normalize_email(payload.email)
        await asyncio.to_thread(update_user_email, int(session["user_id"]), email)
        if payload.new_password:
            password_hash, password_salt = await asyncio.to_thread(
                hash_password, payload.new_password
            )
            await asyncio.to_thread(
                update_user_password, int(session["user_id"]), password_hash, password_salt
            )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    write_audit(
        action="update_own_settings",
        user_id=int(session["user_id"]),
        username=str(session["username"]),
        result="ok",
        details="email_changed=true,password_changed=" + str(bool(payload.new_password)).lower(),
    )
    return JSONResponse({"status": "ok", "email": email})


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _trim_failures(key: str) -> list[float]:
    cutoff = time.monotonic() - _LOGIN_WINDOW_SECONDS
    recent = [stamp for stamp in _login_failures.get(key, []) if stamp >= cutoff]
    if recent:
        _login_failures[key] = recent
    else:
        _login_failures.pop(key, None)
    if len(_login_failures) > 5000:
        for other_key in list(_login_failures):
            kept = [stamp for stamp in _login_failures[other_key] if stamp >= cutoff]
            if kept:
                _login_failures[other_key] = kept
            else:
                _login_failures.pop(other_key, None)
    return recent


@router.post("/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next_url: str = Form(default="/manage"),
):
    key = _client_key(request)
    safe_next = safe_next_url(next_url, "/manage")
    if len(_trim_failures(key)) >= _LOGIN_MAX_FAILURES:
        return RedirectResponse(
            rooted_path(f"/login?error=2&next={quote(safe_next, safe='/')}"),
            status_code=303,
        )

    user = authenticate(username, password)
    if user is None:
        _login_failures[key].append(time.monotonic())
        return RedirectResponse(
            rooted_path(f"/login?error=1&next={quote(safe_next, safe='/')}"),
            status_code=303,
        )

    _login_failures.pop(key, None)
    token, _csrf = create_login_session(int(user["id"]))
    destination = safe_next_url_for_role(
        safe_next,
        str(user["role"]),
        "/manage",
    )
    response = RedirectResponse(rooted_path(destination), status_code=303)
    set_session_cookie(response, token)
    write_audit(
        user_id=int(user["id"]),
        username=str(user["username"]),
        action="login",
        result="ok",
        details=key,
    )
    return response


@router.post("/logout")
async def logout(
    request: Request,
    csrf_token: str = Form(...),
):
    session = current_session(request)
    if session is not None:
        verify_csrf(session, csrf_token)
        write_audit(
            user_id=int(session["user_id"]),
            username=str(session["username"]),
            action="logout",
            result="ok",
        )
    logout_session(request)
    response = RedirectResponse(rooted_path("/login"), status_code=303)
    clear_session_cookie(response)
    return response
