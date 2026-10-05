"""Read-only access to the sibling knowledge portal's existing sessions."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit

from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse


class PortalAuthMiddleware:
    def __init__(self, app, *, database_path: str = "", portal_url: str = "", cookie_name: str = "agent1_session"):
        self.app = app
        self.database_path = database_path
        self.portal_url = portal_url.rstrip("/")
        self.cookie_name = cookie_name
        if database_path:
            target = urlsplit(self.portal_url)
            if target.scheme not in {"http", "https"} or not target.netloc or target.username or target.password or target.query or target.fragment:
                raise ValueError("PORTAL_BASE_URL must be the knowledge portal's HTTP(S) URL")

    def session(self, token: str) -> dict | None:
        if not token or len(token) > 256:
            return None
        uri = Path(self.database_path).resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=2)) as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                "SELECT s.user_id, s.csrf_token, s.expires_at, u.username, u.role, u.is_active "
                "FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?",
                (hashlib.sha256(token.encode()).hexdigest(),),
            ).fetchone()
        if row is None or not row["is_active"]:
            return None
        try:
            if datetime.fromisoformat(row["expires_at"]) <= datetime.now(timezone.utc):
                return None
        except (ValueError, TypeError):
            return None
        return dict(row)

    def login_url(self, path: str) -> str:
        destination = path if path in {"/log", "/grill"} or path.startswith(("/grill/", "/log/")) else "/log"
        return self.portal_url + "/login?next=" + quote("/tools" + destination, safe="/")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request = Request(scope, receive=receive)
        path = request.url.path
        if not self.database_path:
            if path == "/api/account":
                return await JSONResponse({"enabled": False}, headers={"Cache-Control": "no-store"})(scope, receive, send)
            return await self.app(scope, receive, send)
        # Workers retain their independent bearer-token authentication. Static
        # bundles contain no user data and are safe to cache without a session.
        if path.startswith(("/api/worker/", "/assets/")) or path == "/favicon.ico":
            return await self.app(scope, receive, send)
        try:
            session = await asyncio.to_thread(self.session, request.cookies.get(self.cookie_name, ""))
        except (sqlite3.Error, OSError):
            return await JSONResponse({"detail": "Login service temporarily unavailable"}, status_code=503, headers={"Cache-Control": "no-store"})(scope, receive, send)
        if session is None:
            login = self.login_url(path)
            response = (JSONResponse({"detail": "Login required", "login_url": login}, status_code=401)
                        if path.startswith("/api/") else RedirectResponse(login, status_code=303))
            response.headers["Cache-Control"] = "no-store"
            return await response(scope, receive, send)
        if session["role"] not in {"editor", "admin"}:
            return await JSONResponse({"detail": "Editor or admin access required"}, status_code=403, headers={"Cache-Control": "no-store"})(scope, receive, send)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            token = request.headers.get("x-csrf-token", "")
            if not token or not hmac.compare_digest(token, str(session["csrf_token"])):
                return await JSONResponse({"detail": "Invalid CSRF token"}, status_code=403, headers={"Cache-Control": "no-store"})(scope, receive, send)
        if path == "/api/account":
            response = JSONResponse({"enabled": True, "username": session["username"], "role": session["role"],
                                     "csrf_token": session["csrf_token"], "portal_url": self.portal_url},
                                    headers={"Cache-Control": "no-store"})
            return await response(scope, receive, send)
        scope["user"] = session
        if "state" not in scope or not isinstance(scope["state"], dict):
            scope["state"] = {}
        scope["state"]["user"] = session
        async def private_send(message):
            if message["type"] == "http.response.start":
                message["headers"] = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"cache-control"] + [(b"cache-control", b"no-store")]
            await send(message)
        return await self.app(scope, receive, private_send)


def install_portal_auth(app) -> None:
    app.add_middleware(PortalAuthMiddleware, database_path=os.getenv("PORTAL_AUTH_DB", ""),
                       portal_url=os.getenv("PORTAL_BASE_URL", ""),
                       cookie_name=os.getenv("PORTAL_SESSION_COOKIE_NAME", "agent1_session"))
