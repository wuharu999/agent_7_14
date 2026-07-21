from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ecs.app.auth import require_roles, verify_csrf
from ecs.app.config import AUTHORING_COMMAND_TIMEOUT
from ecs.app.database import (
    get_allowed_teams,
    create_authoring_article,
    create_authoring_session,
    get_authoring_article,
    get_authoring_session,
    get_latest_authoring_article,
    update_authoring_article,
    update_authoring_session,
    write_audit,
)
from ecs.app.gateway import gateway

router = APIRouter()
_SESSION_ID = re.compile(r"^auth-[a-f0-9]{24}$")


class SessionRequest(BaseModel):
    team: str = Field(min_length=1, max_length=100)


class MessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=200_000)


class ArticleRequest(BaseModel):
    title: str = Field(min_length=1, max_length=180)
    team: str = Field(min_length=1, max_length=100)
    markdown: str = Field(min_length=1, max_length=500_000)


def _session_id(value: str) -> str:
    if not _SESSION_ID.fullmatch(value):
        raise ValueError("Invalid authoring session")
    return value


def _failure(result: dict) -> JSONResponse | None:
    if result.get("status") != "ok":
        return JSONResponse({"error": str(result.get("error") or "Authoring operation failed")}, status_code=502)
    return None


@router.post("/api/authoring/sessions")
async def create_session(payload: SessionRequest, request: Request, x_csrf_token: str = Header(default="", alias="X-CSRF-Token")):
    session = require_roles(request, {"editor", "admin"})
    verify_csrf(session, x_csrf_token)
    allowed_teams_list = get_allowed_teams()
    if payload.team not in allowed_teams_list:
        return JSONResponse({"error": "invalid team", "allowed_teams": allowed_teams_list}, status_code=400)
    session_id = f"auth-{uuid.uuid4().hex[:24]}"
    create_authoring_session(session_id=session_id, created_by=int(session["user_id"]), team=payload.team)
    try:
        result = await gateway.command(
            "authoring_create",
            timeout=AUTHORING_COMMAND_TIMEOUT,
            session_id=session_id,
            team=payload.team,
        )
    except (ConnectionError, TimeoutError) as exc:
        update_authoring_session(session_id, status="failed")
        return JSONResponse({"error": str(exc)}, status_code=503)
    failure = _failure(result)
    if failure:
        update_authoring_session(session_id, status="failed")
        return failure
    write_audit(user_id=int(session["user_id"]), username=str(session["username"]), action="authoring_create", result="accepted", details=session_id)
    return {"session_id": session_id, "team": payload.team, "messages": []}


@router.get("/api/authoring/sessions/{session_id}")
async def get_session(session_id: str, request: Request):
    session = require_roles(request, {"editor", "admin"})
    try:
        session_id = _session_id(session_id)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    record = get_authoring_session(session_id, int(session["user_id"]))
    if record is None:
        return JSONResponse({"error": "Authoring session not found"}, status_code=404)
    try:
        result = await gateway.command(
            "authoring_history",
            timeout=AUTHORING_COMMAND_TIMEOUT,
            session_id=session_id,
        )
    except (ConnectionError, TimeoutError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)
    failure = _failure(result)
    if failure:
        return failure
    article = get_latest_authoring_article(session_id, int(session["user_id"]))
    return {
        **record,
        "messages": (result.get("messages") if isinstance(result, dict) else []),
        "article": article,
    }


@router.post("/api/authoring/sessions/{session_id}/messages")
async def send_message(session_id: str, payload: MessageRequest, request: Request, x_csrf_token: str = Header(default="", alias="X-CSRF-Token")):
    session = require_roles(request, {"editor", "admin"})
    verify_csrf(session, x_csrf_token)
    try:
        session_id = _session_id(session_id)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    if get_authoring_session(session_id, int(session["user_id"])) is None:
        return JSONResponse({"error": "Authoring session not found"}, status_code=404)
    try:
        result = await gateway.command(
            "authoring_chat",
            timeout=AUTHORING_COMMAND_TIMEOUT,
            session_id=session_id,
            message=payload.message,
        )
    except (ConnectionError, TimeoutError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)
    failure = _failure(result)
    if failure:
        return failure
    return {"answer": result.get("answer")}


@router.post("/api/authoring/sessions/{session_id}/generate")
async def generate_article(session_id: str, request: Request, x_csrf_token: str = Header(default="", alias="X-CSRF-Token")):
    session = require_roles(request, {"editor", "admin"})
    verify_csrf(session, x_csrf_token)
    try:
        session_id = _session_id(session_id)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    record = get_authoring_session(session_id, int(session["user_id"]))
    if record is None:
        return JSONResponse({"error": "Authoring session not found"}, status_code=404)
    try:
        result = await gateway.command(
            "authoring_generate",
            timeout=AUTHORING_COMMAND_TIMEOUT,
            session_id=session_id,
        )
    except (ConnectionError, TimeoutError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)
    failure = _failure(result)
    if failure:
        return failure
    article_id = f"article-{uuid.uuid4().hex[:24]}"
    create_authoring_article(article_id=article_id, session_id=session_id, created_by=int(session["user_id"]), title="", team=str(record["team"]), markdown=str(result.get("markdown") or ""))
    update_authoring_session(session_id, status="article_ready")
    write_audit(user_id=int(session["user_id"]), username=str(session["username"]), action="authoring_generate", result="ok", details=article_id)
    return {"article_id": article_id, "markdown": result.get("markdown"), "team": record["team"]}


@router.post("/api/authoring/articles/{article_id}/publish")
async def publish_article(article_id: str, payload: ArticleRequest, request: Request, x_csrf_token: str = Header(default="", alias="X-CSRF-Token")):
    session = require_roles(request, {"editor", "admin"})
    verify_csrf(session, x_csrf_token)
    article = get_authoring_article(article_id, int(session["user_id"]))
    if article is None:
        return JSONResponse({"error": "Authoring article not found"}, status_code=404)
    allowed_teams_list = get_allowed_teams()
    if payload.team not in allowed_teams_list:
        return JSONResponse({"error": "invalid team", "allowed_teams": allowed_teams_list}, status_code=400)
    title = payload.title.strip()
    if not title:
        return JSONResponse({"error": "Article title cannot be empty"}, status_code=400)
    if not payload.markdown.strip():
        return JSONResponse({"error": "Article Markdown cannot be empty"}, status_code=400)
    update_authoring_article(article_id, title=title, team=payload.team, markdown=payload.markdown, status="publishing", error=None)
    try:
        result = await gateway.command(
            "authoring_publish",
            timeout=AUTHORING_COMMAND_TIMEOUT,
            article_id=article_id,
            team=payload.team,
            title=title,
            markdown=payload.markdown,
        )
    except (ConnectionError, TimeoutError) as exc:
        update_authoring_article(article_id, status="failed", error=str(exc))
        return JSONResponse({"error": str(exc)}, status_code=503)
    failure = _failure(result)
    if failure:
        update_authoring_article(article_id, status="failed", error=str(result.get("error") or ""))
        return failure
    source_path = str(result.get("source_path") or "")
    update_authoring_article(article_id, status="waiting", source_path=source_path, error=None)
    update_authoring_session(str(article["session_id"]), status="published")
    write_audit(user_id=int(session["user_id"]), username=str(session["username"]), action="authoring_publish", source_path=source_path, result="ok", details=article_id)
    return {"status": "waiting", "article_id": article_id, "source_path": source_path}


@router.get("/api/authoring/articles/{article_id}")
async def article_status(article_id: str, request: Request):
    session = require_roles(request, {"editor", "admin"})
    article = get_authoring_article(article_id, int(session["user_id"]))
    if article is None:
        return JSONResponse({"error": "Authoring article not found"}, status_code=404)
    return {
        "article_id": article["article_id"],
        "session_id": article["session_id"],
        "title": article["title"],
        "team": article["team"],
        "status": article["status"],
        "source_path": article["source_path"],
        "error": article["error"],
        "updated_at": article["updated_at"],
    }
