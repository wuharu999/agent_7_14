from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ecs.app.config import DATABASE_PATH

_DB_LOCK = threading.RLock()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DATABASE_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=5000")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}


def initialize_database() -> None:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _DB_LOCK, _connect() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                password_salt TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('viewer', 'editor', 'admin')),
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                csrf_token TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS uploads (
                upload_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL UNIQUE,
                team TEXT NOT NULL,
                filename TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                ecs_path TEXT NOT NULL,
                status TEXT NOT NULL,
                stage TEXT NOT NULL,
                message TEXT NOT NULL DEFAULT '',
                percent INTEGER,
                error TEXT,
                published_at_ms INTEGER,
                created_by INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(created_by) REFERENCES users(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS upload_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                upload_id TEXT NOT NULL,
                source_identity TEXT NOT NULL,
                status TEXT NOT NULL,
                error TEXT,
                files_written TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL,
                UNIQUE(upload_id, source_identity),
                FOREIGN KEY(upload_id) REFERENCES uploads(upload_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS file_audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                username TEXT NOT NULL,
                action TEXT NOT NULL,
                source_path TEXT NOT NULL DEFAULT '',
                result TEXT NOT NULL,
                details TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL
            );

            CREATE INDEX IF NOT EXISTS idx_uploads_status ON uploads(status);
            CREATE INDEX IF NOT EXISTS idx_sources_upload ON upload_sources(upload_id);
            CREATE INDEX IF NOT EXISTS idx_sessions_token ON sessions(token_hash);
            CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expires_at);
            CREATE INDEX IF NOT EXISTS idx_audit_created ON file_audit_log(created_at);
            """
        )
        # Migrate databases created by the earlier prototype.
        if "created_by" not in _columns(connection, "uploads"):
            connection.execute("ALTER TABLE uploads ADD COLUMN created_by INTEGER")


# ---------------------------------------------------------------------------
# Authentication and audit
# ---------------------------------------------------------------------------

def create_user_record(
    *, username: str, password_hash: str, password_salt: str, role: str
) -> int:
    now = utc_now()
    with _DB_LOCK, _connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO users (
                username, password_hash, password_salt, role,
                is_active, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 1, ?, ?)
            """,
            (username, password_hash, password_salt, role, now, now),
        )
        return int(cursor.lastrowid)


def update_user_record(
    user_id: int, password_hash: str, password_salt: str, role: str
) -> None:
    with _DB_LOCK, _connect() as connection:
        connection.execute(
            "UPDATE users SET password_hash = ?, password_salt = ?, role = ?, "
            "is_active = 1, updated_at = ? WHERE id = ?",
            (password_hash, password_salt, role, utc_now(), user_id),
        )


def get_user_by_username(username: str) -> dict[str, Any] | None:
    with _DB_LOCK, _connect() as connection:
        row = connection.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
    return dict(row) if row else None


def get_user_by_id(user_id: int) -> dict[str, Any] | None:
    with _DB_LOCK, _connect() as connection:
        row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def list_users() -> list[dict[str, Any]]:
    with _DB_LOCK, _connect() as connection:
        rows = connection.execute(
            "SELECT id, username, role, is_active, created_at, updated_at FROM users ORDER BY username"
        ).fetchall()
    return [dict(row) for row in rows]


def create_session_record(
    *, user_id: int, token_hash: str, csrf_token: str, expires_at: str
) -> None:
    with _DB_LOCK, _connect() as connection:
        connection.execute(
            """
            INSERT INTO sessions (user_id, token_hash, csrf_token, expires_at, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, token_hash, csrf_token, expires_at, utc_now()),
        )


def get_session_with_user(token_hash: str) -> dict[str, Any] | None:
    now = utc_now()
    with _DB_LOCK, _connect() as connection:
        row = connection.execute(
            """
            SELECT
                s.id AS session_id,
                s.user_id,
                s.csrf_token,
                s.expires_at,
                u.username,
                u.role,
                u.is_active
            FROM sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token_hash = ? AND s.expires_at > ?
            """,
            (token_hash, now),
        ).fetchone()
    return dict(row) if row else None


def delete_session_by_hash(token_hash: str) -> None:
    with _DB_LOCK, _connect() as connection:
        connection.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))


def delete_expired_sessions() -> None:
    with _DB_LOCK, _connect() as connection:
        connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (utc_now(),))


def write_audit(
    *,
    user_id: int | None,
    username: str,
    action: str,
    source_path: str = "",
    result: str,
    details: str = "",
) -> None:
    with _DB_LOCK, _connect() as connection:
        connection.execute(
            """
            INSERT INTO file_audit_log (
                user_id, username, action, source_path, result, details, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, username, action, source_path, result, details, utc_now()),
        )


def list_audit_log(limit: int = 100) -> list[dict[str, Any]]:
    with _DB_LOCK, _connect() as connection:
        rows = connection.execute(
            "SELECT * FROM file_audit_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Upload and ingestion status
# ---------------------------------------------------------------------------

def create_upload(
    *,
    upload_id: str,
    task_id: str,
    team: str,
    filename: str,
    size_bytes: int,
    ecs_path: str,
    status: str,
    stage: str,
    message: str,
    created_by: int | None = None,
) -> None:
    now = utc_now()
    with _DB_LOCK, _connect() as connection:
        connection.execute(
            """
            INSERT INTO uploads (
                upload_id, task_id, team, filename, size_bytes, ecs_path,
                status, stage, message, created_by, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                upload_id,
                task_id,
                team,
                filename,
                size_bytes,
                ecs_path,
                status,
                stage,
                message,
                created_by,
                now,
                now,
            ),
        )


def update_upload(upload_id: str, **fields: Any) -> None:
    allowed = {
        "status",
        "stage",
        "message",
        "percent",
        "error",
        "published_at_ms",
    }
    updates = {key: value for key, value in fields.items() if key in allowed}
    if not updates:
        return
    updates["updated_at"] = utc_now()
    assignments = ", ".join(f"{key} = ?" for key in updates)
    values = list(updates.values()) + [upload_id]
    with _DB_LOCK, _connect() as connection:
        connection.execute(
            f"UPDATE uploads SET {assignments} WHERE upload_id = ?",
            values,
        )


def upsert_source(
    *,
    upload_id: str,
    source_identity: str,
    status: str,
    error: str | None = None,
    files_written: list[str] | None = None,
) -> None:
    with _DB_LOCK, _connect() as connection:
        connection.execute(
            """
            INSERT INTO upload_sources (
                upload_id, source_identity, status, error, files_written, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(upload_id, source_identity) DO UPDATE SET
                status = excluded.status,
                error = excluded.error,
                files_written = excluded.files_written,
                updated_at = excluded.updated_at
            """,
            (
                upload_id,
                source_identity,
                status,
                error,
                json.dumps(files_written or [], ensure_ascii=False),
                utc_now(),
            ),
        )
    refresh_upload_aggregate(upload_id)


def mark_sources_deleted(source_path: str) -> None:
    normalized = source_path.strip("/")
    prefix = normalized + "/"
    prefix_length = len(prefix)
    with _DB_LOCK, _connect() as connection:
        rows = connection.execute(
            "SELECT DISTINCT upload_id FROM upload_sources "
            "WHERE source_identity = ? OR substr(source_identity, 1, ?) = ?",
            (normalized, prefix_length, prefix),
        ).fetchall()
        connection.execute(
            "UPDATE upload_sources SET status = 'deleted', updated_at = ? "
            "WHERE source_identity = ? OR substr(source_identity, 1, ?) = ?",
            (utc_now(), normalized, prefix_length, prefix),
        )
    for row in rows:
        refresh_upload_aggregate(str(row["upload_id"]))


def register_sources(upload_id: str, source_identities: list[str], published_at_ms: int) -> None:
    update_upload(
        upload_id,
        status="waiting_for_llm_wiki",
        stage="waiting_for_llm_wiki",
        message=f"Published {len(source_identities)} source file(s); waiting for LLM Wiki",
        percent=None,
        published_at_ms=published_at_ms,
        error=None,
    )
    for source_identity in source_identities:
        upsert_source(
            upload_id=upload_id,
            source_identity=source_identity,
            status="waiting",
        )


def refresh_upload_aggregate(upload_id: str) -> None:
    with _DB_LOCK, _connect() as connection:
        rows = connection.execute(
            "SELECT status, COUNT(*) AS count FROM upload_sources WHERE upload_id = ? GROUP BY status",
            (upload_id,),
        ).fetchall()
    counts = {row["status"]: int(row["count"]) for row in rows}
    deleted = counts.get("deleted", 0)
    active_total = sum(counts.values()) - deleted
    if active_total <= 0:
        if deleted:
            update_upload(
                upload_id,
                status="sources_removed",
                stage="sources_removed",
                message="All published source files were removed",
                percent=100,
                error=None,
            )
        return

    completed = counts.get("completed", 0)
    failed = counts.get("failed", 0)
    processing = counts.get("processing", 0)
    queued = counts.get("queued", 0)
    waiting = counts.get("waiting", 0)
    terminal = completed + failed
    percent = int((terminal / active_total) * 100)

    if failed == active_total:
        status = "failed"
        stage = "ingestion_failed"
        message = f"All {active_total} source files failed ingestion"
    elif terminal == active_total:
        if failed:
            status = "partially_failed"
            stage = "ingestion_partially_failed"
            message = f"Ingestion finished: {completed} completed, {failed} failed"
        else:
            status = "completed"
            stage = "ingestion_completed"
            message = f"All {completed} source files completed ingestion"
    elif processing:
        status = "ingesting"
        stage = "llm_wiki_ingestion"
        message = f"LLM Wiki ingesting: {completed}/{active_total} completed, {processing} processing"
    elif queued:
        status = "queued_in_llm_wiki"
        stage = "queued_in_llm_wiki"
        message = f"Queued in LLM Wiki: {queued} queued, {completed}/{active_total} completed"
    else:
        status = "waiting_for_llm_wiki"
        stage = "waiting_for_llm_wiki"
        message = f"Waiting for LLM Wiki to detect {waiting} source file(s)"

    update_upload(
        upload_id,
        status=status,
        stage=stage,
        message=message,
        percent=percent,
        error=None,
    )


def get_upload(upload_id: str) -> dict[str, Any] | None:
    with _DB_LOCK, _connect() as connection:
        upload = connection.execute(
            "SELECT * FROM uploads WHERE upload_id = ?",
            (upload_id,),
        ).fetchone()
        if upload is None:
            return None
        source_rows = connection.execute(
            "SELECT source_identity, status, error, files_written, updated_at "
            "FROM upload_sources WHERE upload_id = ? ORDER BY source_identity",
            (upload_id,),
        ).fetchall()

    data = dict(upload)
    sources: list[dict[str, Any]] = []
    counts = {
        "waiting": 0,
        "queued": 0,
        "processing": 0,
        "completed": 0,
        "failed": 0,
        "deleted": 0,
    }
    for row in source_rows:
        item = dict(row)
        try:
            item["files_written"] = json.loads(item.get("files_written") or "[]")
        except json.JSONDecodeError:
            item["files_written"] = []
        sources.append(item)
        status = item["status"]
        if status in counts:
            counts[status] += 1

    data["sources"] = sources
    data["progress"] = {"total": len(sources), **counts}
    return data


def list_uploads(limit: int = 50) -> list[dict[str, Any]]:
    with _DB_LOCK, _connect() as connection:
        rows = connection.execute(
            "SELECT upload_id, team, filename, size_bytes, status, stage, message, "
            "percent, error, created_by, created_at, updated_at FROM uploads "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def list_dispatchable_uploads() -> list[dict[str, Any]]:
    statuses = (
        "waiting_for_worker",
        "queued_for_worker",
        "worker_disconnected",
        "download_failed",
    )
    placeholders = ",".join("?" for _ in statuses)
    with _DB_LOCK, _connect() as connection:
        rows = connection.execute(
            f"SELECT * FROM uploads WHERE status IN ({placeholders}) ORDER BY created_at",
            statuses,
        ).fetchall()
    return [dict(row) for row in rows]
