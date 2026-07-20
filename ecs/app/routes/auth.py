from __future__ import annotations

import time
from collections import defaultdict
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from ecs.app.auth import (
    authenticate,
    clear_session_cookie,
    create_login_session,
    current_session,
    logout_session,
    safe_next_url,
    set_session_cookie,
    verify_csrf,
)
from ecs.app.database import write_audit

router = APIRouter()
_LOGIN_WINDOW_SECONDS = 15 * 60
_LOGIN_MAX_FAILURES = 5
_login_failures: dict[str, list[float]] = defaultdict(list)


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
    email: str = Form(...),
    password: str = Form(...),
    next_url: str = Form(default="/manage"),
):
    key = _client_key(request)
    safe_next = safe_next_url(next_url, "/manage")
    if len(_trim_failures(key)) >= _LOGIN_MAX_FAILURES:
        return RedirectResponse(
            f"/login?error=2&next={quote(safe_next, safe='/')}",
            status_code=303,
        )

    user = authenticate(email, password)
    if user is None:
        _login_failures[key].append(time.monotonic())
        return RedirectResponse(
            f"/login?error=1&next={quote(safe_next, safe='/')}",
            status_code=303,
        )

    _login_failures.pop(key, None)
    token, _csrf = create_login_session(int(user["id"]))
    response = RedirectResponse(safe_next, status_code=303)
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
    response = RedirectResponse("/login", status_code=303)
    clear_session_cookie(response)
    return response
