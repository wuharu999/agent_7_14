from __future__ import annotations

import asyncio
import json
import html
from typing import Any
from fastapi import APIRouter, Header, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from ecs.app.auth import (
    create_or_update_user,
    current_session,
    hash_password,
    normalize_email,
    normalize_username,
    require_roles,
    verify_csrf,
)
from ecs.app.database import (
    get_allowed_teams,
    get_user_by_id,
    get_user_by_username,
    get_user_by_email,
    list_users,
    delete_disabled_user,
    toggle_user_active,
    update_user_details,
    update_user_password,
    write_audit,
)

router = APIRouter()


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=10, max_length=256)
    role: str = Field(pattern=r"^(editor|admin)$")
    teams: list[str] = Field(default_factory=list)


class UpdateUserRequest(BaseModel):
    role: str = Field(pattern=r"^(editor|admin)$")
    teams: list[str] = Field(default_factory=list)
    password: str | None = Field(default=None, min_length=10, max_length=256)


def _template(name: str) -> str:
    from pathlib import Path
    template_path = Path(__file__).resolve().parents[1] / "templates" / name
    return template_path.read_text(encoding="utf-8")


@router.get("/admin/users", response_class=HTMLResponse)
async def admin_users_page(request: Request):
    session = current_session(request)
    if session is None:
        return RedirectResponse("/login?next=/admin/users", status_code=303)
    if session["role"] != "admin":
        return RedirectResponse("/manage", status_code=303)
    page = _template("admin_users.html")
    page = page.replace("__CSRF_TOKEN__", html.escape(str(session["csrf_token"]), quote=True))
    page = page.replace("__USERNAME__", html.escape(str(session["username"])))
    page = page.replace("__ALLOWED_TEAMS__", json.dumps(get_allowed_teams(), ensure_ascii=False))
    return HTMLResponse(page)


@router.get("/api/admin/users")
async def api_list_users(request: Request):
    session = require_roles(request, {"admin"})
    users = list_users()
    return JSONResponse({"status": "ok", "users": users, "allowed_teams": get_allowed_teams()})


@router.post("/api/admin/users/create")
async def api_create_user(
    payload: CreateUserRequest,
    request: Request,
    x_csrf_token: str = Header(default="", alias="X-CSRF-Token"),
):
    session = require_roles(request, {"admin"})
    verify_csrf(session, x_csrf_token)

    try:
        username = normalize_username(payload.username)
        email = normalize_email(payload.email)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    if get_user_by_username(username):
        return JSONResponse({"error": "Username already exists"}, status_code=400)
    if get_user_by_email(email):
        return JSONResponse({"error": "Email already registered"}, status_code=400)

    # Validate team choices against database robots
    valid_teams = [t for t in payload.teams if t in get_allowed_teams()]
    teams_str = ",".join(valid_teams)

    # Password hashing is deliberately expensive. Keep it off the single
    # Uvicorn event loop so the public HTTP connection and Worker WebSocket
    # remain responsive while an account is created.
    user_id = await asyncio.to_thread(
        create_or_update_user,
        username=username,
        email=email,
        password=payload.password,
        role=payload.role,
        teams=teams_str,
    )

    await asyncio.to_thread(
        write_audit,
        action="create_user",
        user_id=int(session["user_id"]),
        username=str(session.get("username", "")),
        result="ok",
        details=json.dumps(
            {
                "created_user_id": user_id,
                "username": username,
                "role": payload.role,
                "teams": teams_str,
            }
        ),
    )

    return JSONResponse({"status": "ok", "user_id": user_id, "username": username})


@router.post("/api/admin/users/{user_id}/update")
async def api_update_user(
    user_id: int,
    payload: UpdateUserRequest,
    request: Request,
    x_csrf_token: str = Header(default="", alias="X-CSRF-Token"),
):
    session = require_roles(request, {"admin"})
    verify_csrf(session, x_csrf_token)

    target_user = get_user_by_id(user_id)
    if not target_user:
        return JSONResponse({"error": "User not found"}, status_code=404)

    valid_teams = [t for t in payload.teams if t in get_allowed_teams()]
    teams_str = ",".join(valid_teams)

    update_user_details(user_id, role=payload.role, teams=teams_str)

    if payload.password and len(payload.password) >= 10:
        pwd_hash, pwd_salt = await asyncio.to_thread(hash_password, payload.password)
        await asyncio.to_thread(
            update_user_password, user_id, pwd_hash, pwd_salt
        )

    write_audit(
        action="update_user",
        user_id=int(session["user_id"]),
        username=str(session.get("username", "")),
        result="ok",
        details=json.dumps({"updated_user_id": user_id, "role": payload.role, "teams": teams_str, "password_changed": bool(payload.password)}),
    )

    return JSONResponse({"status": "ok", "user_id": user_id})


@router.post("/api/admin/users/{user_id}/toggle_active")
async def api_toggle_user_active(
    user_id: int,
    request: Request,
    x_csrf_token: str = Header(default="", alias="X-CSRF-Token"),
):
    session = require_roles(request, {"admin"})
    verify_csrf(session, x_csrf_token)

    if int(session["user_id"]) == user_id:
        return JSONResponse({"error": "Cannot deactivate your own active session"}, status_code=400)

    try:
        new_state = toggle_user_active(user_id)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)

    write_audit(
        action="toggle_user_active",
        user_id=int(session["user_id"]),
        username=str(session.get("username", "")),
        result="ok",
        details=json.dumps({"target_user_id": user_id, "new_active_state": new_state}),
    )

    return JSONResponse({"status": "ok", "user_id": user_id, "is_active": new_state})


@router.delete("/api/admin/users/{user_id}")
async def api_delete_disabled_user(
    user_id: int,
    request: Request,
    x_csrf_token: str = Header(default="", alias="X-CSRF-Token"),
):
    session = require_roles(request, {"admin"})
    verify_csrf(session, x_csrf_token)

    try:
        deleted = await asyncio.to_thread(delete_disabled_user, user_id)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    write_audit(
        action="delete_disabled_user",
        user_id=int(session["user_id"]),
        username=str(session.get("username", "")),
        result="ok",
        details=json.dumps(
            {"deleted_user_id": deleted["id"], "deleted_username": deleted["username"]}
        ),
    )
    return JSONResponse({"status": "ok", "user_id": deleted["id"]})
