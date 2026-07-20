from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, status
from fastapi.responses import Response

from ecs.app.config import (
    COOKIE_SAMESITE,
    COOKIE_SECURE,
    SESSION_COOKIE_NAME,
    SESSION_HOURS,
)
from ecs.app.database import (
    create_session_record,
    create_user_record,
    delete_session_by_hash,
    get_session_with_user,
    get_user_by_username,
    get_user_by_email,
    update_user_record,
)

_PASSWORD_N = 2**14
_PASSWORD_R = 8
_PASSWORD_P = 1
_PASSWORD_LENGTH = 32


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value.encode("ascii"))


def hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    if len(password) < 10:
        raise ValueError("Password must contain at least 10 characters")
    actual_salt = salt or secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=actual_salt,
        n=_PASSWORD_N,
        r=_PASSWORD_R,
        p=_PASSWORD_P,
        dklen=_PASSWORD_LENGTH,
    )
    return _b64encode(derived), _b64encode(actual_salt)


def verify_password(password: str, stored_hash: str, stored_salt: str) -> bool:
    try:
        calculated, _ = hash_password(password, _b64decode(stored_salt))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(calculated, stored_hash)


def normalize_username(username: str) -> str:
    value = username.strip().lower()
    if not value or len(value) > 64:
        raise ValueError("Username must contain 1 to 64 characters")
    if any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789._-" for ch in value):
        raise ValueError("Username may contain letters, numbers, dot, underscore and hyphen")
    return value


def normalize_email(email: str) -> str:
    value = email.strip().lower()
    if not value or len(value) > 255 or "@" not in value:
        raise ValueError("Invalid email address")
    return value


def create_or_update_user(username: str, email: str, password: str, role: str, teams: str = "") -> int:
    normalized_username = normalize_username(username)
    normalized_email = normalize_email(email)
    if role not in {"viewer", "editor", "admin"}:
        raise ValueError("Role must be viewer, editor or admin")
    password_hash, password_salt = hash_password(password)
    existing = get_user_by_email(normalized_email) or get_user_by_username(normalized_username)
    if existing:
        update_user_record(int(existing["id"]), normalized_email, password_hash, password_salt, role, teams)
        return int(existing["id"])
    return create_user_record(
        username=normalized_username,
        email=normalized_email,
        password_hash=password_hash,
        password_salt=password_salt,
        role=role,
        teams=teams,
    )


def authenticate(username: str, password: str) -> dict[str, Any] | None:
    try:
        normalized = normalize_username(username)
    except ValueError:
        return None
    user = get_user_by_username(normalized)
    if not user or not bool(user.get("is_active")):
        return None
    if not verify_password(password, str(user["password_hash"]), str(user["password_salt"])):
        return None
    return user


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_login_session(user_id: int) -> tuple[str, str]:
    token = secrets.token_urlsafe(48)
    csrf_token = secrets.token_urlsafe(32)
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=SESSION_HOURS)).isoformat()
    create_session_record(
        user_id=user_id,
        token_hash=_token_hash(token),
        csrf_token=csrf_token,
        expires_at=expires_at,
    )
    return token, csrf_token


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=SESSION_HOURS * 3600,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite=COOKIE_SAMESITE,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        secure=COOKIE_SECURE,
        httponly=True,
        samesite=COOKIE_SAMESITE,
    )


def current_session(request: Request) -> dict[str, Any] | None:
    token = request.cookies.get(SESSION_COOKIE_NAME, "")
    if not token:
        return None
    session = get_session_with_user(_token_hash(token))
    if not session or not bool(session.get("is_active")):
        return None
    session["raw_token"] = token
    return session


def logout_session(request: Request) -> None:
    token = request.cookies.get(SESSION_COOKIE_NAME, "")
    if token:
        delete_session_by_hash(_token_hash(token))


def require_user(request: Request) -> dict[str, Any]:
    session = current_session(request)
    if session is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Login required")
    return session


def require_roles(request: Request, roles: Iterable[str]) -> dict[str, Any]:
    session = require_user(request)
    allowed = set(roles)
    if str(session.get("role")) not in allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permission")
    return session


def verify_csrf(session: dict[str, Any], provided: str | None) -> None:
    expected = str(session.get("csrf_token") or "")
    if not expected or not provided or not hmac.compare_digest(expected, provided):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")


def safe_next_url(value: str | None, default: str = "/manage") -> str:
    if not value:
        return default
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or not value.startswith("/") or value.startswith("//"):
        return default
    return value


def check_robot_access(session: dict[str, Any], robot_path: str) -> bool:
    if session.get("role") == "admin":
        return True
    from ecs.app.database import _DB_LOCK, _connect
    with _DB_LOCK, _connect() as connection:
        row = connection.execute(
            """
            SELECT r.id FROM robots r
            JOIN robot_editors re ON r.id = re.robot_id
            WHERE r.storage_path = ? AND re.user_id = ?
            """, (robot_path, session["user_id"])
        ).fetchone()
        return bool(row)

