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
                teams TEXT NOT NULL DEFAULT '',
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
                security_scan_complete INTEGER NOT NULL DEFAULT 0,
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
                retry_count INTEGER NOT NULL DEFAULT 0,
                max_retries INTEGER NOT NULL DEFAULT 0,
                active_queue_count INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                UNIQUE(upload_id, source_identity),
                FOREIGN KEY(upload_id) REFERENCES uploads(upload_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS upload_security_warnings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                upload_id TEXT NOT NULL,
                source_identity TEXT NOT NULL,
                categories TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
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

            CREATE TABLE IF NOT EXISTS authoring_sessions (
                session_id TEXT PRIMARY KEY,
                created_by INTEGER NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                team TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'draft',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(created_by) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS authoring_articles (
                article_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                created_by INTEGER NOT NULL,
                title TEXT NOT NULL,
                team TEXT NOT NULL,
                markdown TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'draft',
                source_path TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(session_id) REFERENCES authoring_sessions(session_id) ON DELETE CASCADE,
                FOREIGN KEY(created_by) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_uploads_status ON uploads(status);
            CREATE INDEX IF NOT EXISTS idx_sources_upload ON upload_sources(upload_id);
            CREATE INDEX IF NOT EXISTS idx_security_warnings_upload
                ON upload_security_warnings(upload_id);
            CREATE INDEX IF NOT EXISTS idx_sessions_token ON sessions(token_hash);
            CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expires_at);
            CREATE INDEX IF NOT EXISTS idx_audit_created ON file_audit_log(created_at);
            CREATE INDEX IF NOT EXISTS idx_authoring_sessions_user ON authoring_sessions(created_by, updated_at);
            CREATE INDEX IF NOT EXISTS idx_authoring_articles_session ON authoring_articles(session_id, updated_at);
            """
        )
        # Migrate databases created by the earlier prototype.
        if "created_by" not in _columns(connection, "uploads"):
            connection.execute("ALTER TABLE uploads ADD COLUMN created_by INTEGER")
        if "security_scan_complete" not in _columns(connection, "uploads"):
            connection.execute(
                "ALTER TABLE uploads ADD COLUMN security_scan_complete "
                "INTEGER NOT NULL DEFAULT 1"
            )
        if "retry_count" not in _columns(connection, "upload_sources"):
            connection.execute("ALTER TABLE upload_sources ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0")
        if "max_retries" not in _columns(connection, "upload_sources"):
            connection.execute("ALTER TABLE upload_sources ADD COLUMN max_retries INTEGER NOT NULL DEFAULT 0")
        if "active_queue_count" not in _columns(connection, "upload_sources"):
            connection.execute("ALTER TABLE upload_sources ADD COLUMN active_queue_count INTEGER NOT NULL DEFAULT 0")
        if "teams" not in _columns(connection, "users"):
            connection.execute("ALTER TABLE users ADD COLUMN teams TEXT NOT NULL DEFAULT ''")
        if "email" not in _columns(connection, "users"):
            connection.execute("ALTER TABLE users ADD COLUMN email TEXT")
            connection.execute("UPDATE users SET email = username || '@localhost' WHERE email IS NULL")
            connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email)")

        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS qa_visitors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ip_address TEXT NOT NULL,
                user_id INTEGER,
                visited_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS wiki_metrics (
                date TEXT PRIMARY KEY,
                new_entries INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS team_settings (
                team_name TEXT PRIMARY KEY,
                auto_review_enabled INTEGER NOT NULL DEFAULT 0,
                last_review_at TEXT,
                prev_review_at TEXT
            );

            CREATE TABLE IF NOT EXISTS team_members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                team_name TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('member', 'captain')),
                joined_at TEXT NOT NULL,
                UNIQUE(team_name, user_id),
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS team_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                team_name TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending', 'approved', 'denied')),
                requested_at TEXT NOT NULL,
                UNIQUE(team_name, user_id),
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            """
        )


# ---------------------------------------------------------------------------
# Authentication and audit
# ---------------------------------------------------------------------------

def create_user_record(
    *, username: str, email: str, password_hash: str, password_salt: str, role: str, teams: str = ""
) -> int:
    now = utc_now()
    with _DB_LOCK, _connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO users (
                username, email, password_hash, password_salt, role, teams,
                is_active, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (username, email, password_hash, password_salt, role, teams, now, now),
        )
        return int(cursor.lastrowid)


def update_user_record(
    user_id: int, email: str, password_hash: str, password_salt: str, role: str, teams: str = ""
) -> None:
    with _DB_LOCK, _connect() as connection:
        connection.execute(
            "UPDATE users SET email = ?, password_hash = ?, password_salt = ?, role = ?, teams = ?, "
            "is_active = 1, updated_at = ? WHERE id = ?",
            (email, password_hash, password_salt, role, teams, utc_now(), user_id),
        )


def get_user_by_email(email: str) -> dict[str, Any] | None:
    with _DB_LOCK, _connect() as connection:
        row = connection.execute(
            "SELECT * FROM users WHERE email = ?", (email,)
        ).fetchone()
    return dict(row) if row else None


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
            "SELECT id, username, role, teams, is_active, created_at, updated_at FROM users ORDER BY username"
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
                u.teams,
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


def get_recent_upload_count(user_id: int, minutes: int = 1) -> int:
    from ecs.app.database import _DB_LOCK, _connect
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
    with _DB_LOCK, _connect() as connection:
        row = connection.execute(
            "SELECT COUNT(*) as count FROM file_audit_log WHERE user_id = ? AND action = 'upload_source' AND created_at >= ?",
            (user_id, cutoff)
        ).fetchone()
        return int(row["count"]) if row else 0


def get_team_captains(team_name: str) -> list[dict]:
    from ecs.app.database import _DB_LOCK, _connect
    with _DB_LOCK, _connect() as connection:
        rows = connection.execute(
            """
            SELECT users.* FROM users
            JOIN team_members ON users.id = team_members.user_id
            WHERE team_members.team_name = ? AND team_members.role = 'captain'
            """, (team_name,)
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
                status, stage, message, security_scan_complete, created_by,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
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
        "security_scan_complete",
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
    retry_count: int = 0,
    max_retries: int = 0,
    active_queue_count: int = 0,
) -> None:
    with _DB_LOCK, _connect() as connection:
        connection.execute(
            """
            INSERT INTO upload_sources (
                upload_id, source_identity, status, error, files_written,
                retry_count, max_retries, active_queue_count, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(upload_id, source_identity) DO UPDATE SET
                status = excluded.status,
                error = excluded.error,
                files_written = excluded.files_written,
                retry_count = excluded.retry_count,
                max_retries = excluded.max_retries,
                active_queue_count = excluded.active_queue_count,
                updated_at = excluded.updated_at
            """,
            (
                upload_id,
                source_identity,
                status,
                error,
                json.dumps(files_written or [], ensure_ascii=False),
                retry_count,
                max_retries,
                active_queue_count,
                utc_now(),
            ),
        )
    refresh_upload_aggregate(upload_id)


def replace_upload_security_warnings(
    upload_id: str,
    warnings: list[dict[str, Any]],
    *,
    complete: bool,
) -> None:
    now = utc_now()
    with _DB_LOCK, _connect() as connection:
        connection.execute(
            "DELETE FROM upload_security_warnings WHERE upload_id = ?",
            (upload_id,),
        )
        connection.executemany(
            "INSERT INTO upload_security_warnings "
            "(upload_id, source_identity, categories, created_at) "
            "VALUES (?, ?, ?, ?)",
            [
                (
                    upload_id,
                    str(warning["source_identity"]),
                    json.dumps(warning["categories"], ensure_ascii=False),
                    now,
                )
                for warning in warnings
            ],
        )
        connection.execute(
            "UPDATE uploads SET security_scan_complete = ?, updated_at = ? "
            "WHERE upload_id = ?",
            (1 if complete else 0, now, upload_id),
        )


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


def get_team_upload_usage(team: str) -> int:
    with _DB_LOCK, _connect() as connection:
        row = connection.execute(
            "SELECT SUM(size_bytes) as total_size FROM uploads "
            "WHERE team = ? AND status != 'deleted' AND stage != 'sources_removed'",
            (team,)
        ).fetchone()
    return int(row["total_size"]) if row and row["total_size"] else 0


def get_upload(upload_id: str) -> dict[str, Any] | None:
    with _DB_LOCK, _connect() as connection:
        upload = connection.execute(
            "SELECT * FROM uploads WHERE upload_id = ?",
            (upload_id,),
        ).fetchone()
        if upload is None:
            return None
        source_rows = connection.execute(
            "SELECT source_identity, status, error, files_written, retry_count, max_retries, active_queue_count, updated_at "
            "FROM upload_sources WHERE upload_id = ? ORDER BY source_identity",
            (upload_id,),
        ).fetchall()
        warning_rows = connection.execute(
            "SELECT source_identity, categories, created_at "
            "FROM upload_security_warnings WHERE upload_id = ? "
            "ORDER BY source_identity",
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
    security_warnings: list[dict[str, Any]] = []
    for row in warning_rows:
        warning = dict(row)
        try:
            categories = json.loads(warning.get("categories") or "[]")
        except json.JSONDecodeError:
            categories = ["scan_incomplete_metadata"]
        warning["categories"] = categories if isinstance(categories, list) else []
        security_warnings.append(warning)
    data["security_warnings"] = security_warnings
    data["security_scan_complete"] = bool(data.get("security_scan_complete"))
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


def get_all_upload_timestamps() -> dict[str, str]:
    with _DB_LOCK, _connect() as connection:
        rows = connection.execute(
            "SELECT upload_id, created_at FROM uploads"
        ).fetchall()
    return {row["upload_id"]: row["created_at"] for row in rows}


# ---------------------------------------------------------------------------
# Claude authoring sessions and reviewed articles

# ---------------------------------------------------------------------------

def create_authoring_session(*, session_id: str, created_by: int, team: str) -> None:
    now = utc_now()
    with _DB_LOCK, _connect() as connection:
        connection.execute(
            "INSERT INTO authoring_sessions "
            "(session_id, created_by, team, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (session_id, created_by, team, now, now),
        )


def get_authoring_session(session_id: str, user_id: int | None = None) -> dict[str, Any] | None:
    query = "SELECT * FROM authoring_sessions WHERE session_id = ?"
    values: list[Any] = [session_id]
    if user_id is not None:
        query += " AND created_by = ?"
        values.append(user_id)
    with _DB_LOCK, _connect() as connection:
        row = connection.execute(query, values).fetchone()
    return dict(row) if row else None


def update_authoring_session(session_id: str, **fields: Any) -> None:
    allowed = {"title", "team", "status"}
    updates = {key: value for key, value in fields.items() if key in allowed}
    if not updates:
        return
    updates["updated_at"] = utc_now()
    assignments = ", ".join(f"{key} = ?" for key in updates)
    with _DB_LOCK, _connect() as connection:
        connection.execute(
            f"UPDATE authoring_sessions SET {assignments} WHERE session_id = ?",
            [*updates.values(), session_id],
        )


def create_authoring_article(
    *, article_id: str, session_id: str, created_by: int, title: str, team: str, markdown: str
) -> None:
    now = utc_now()
    with _DB_LOCK, _connect() as connection:
        connection.execute(
            "INSERT INTO authoring_articles "
            "(article_id, session_id, created_by, title, team, markdown, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (article_id, session_id, created_by, title, team, markdown, now, now),
        )


def get_authoring_article(article_id: str, user_id: int | None = None) -> dict[str, Any] | None:
    query = "SELECT * FROM authoring_articles WHERE article_id = ?"
    values: list[Any] = [article_id]
    if user_id is not None:
        query += " AND created_by = ?"
        values.append(user_id)
    with _DB_LOCK, _connect() as connection:
        row = connection.execute(query, values).fetchone()
    return dict(row) if row else None


def get_latest_authoring_article(
    session_id: str, user_id: int
) -> dict[str, Any] | None:
    with _DB_LOCK, _connect() as connection:
        row = connection.execute(
            "SELECT * FROM authoring_articles "
            "WHERE session_id = ? AND created_by = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (session_id, user_id),
        ).fetchone()
    return dict(row) if row else None


def update_authoring_article(article_id: str, **fields: Any) -> None:
    allowed = {"title", "team", "markdown", "status", "source_path", "error"}
    updates = {key: value for key, value in fields.items() if key in allowed}
    if not updates:
        return
    updates["updated_at"] = utc_now()
    assignments = ", ".join(f"{key} = ?" for key in updates)
    with _DB_LOCK, _connect() as connection:
        connection.execute(
            f"UPDATE authoring_articles SET {assignments} WHERE article_id = ?",
            [*updates.values(), article_id],
        )


def reconcile_existing_uploads(uploads_on_disk: list[dict[str, str]]) -> None:
    existing_set = {(item["team"], item["upload_id"]) for item in uploads_on_disk}
    
    with _DB_LOCK, _connect() as connection:
        rows = connection.execute(
            "SELECT upload_id, team, status FROM uploads WHERE status IN ('completed', 'sources_removed')"
        ).fetchall()
        
        now = utc_now()
        updated_upload_ids = []
        for row in rows:
            uid = str(row["upload_id"])
            team = str(row["team"])
            current_status = str(row["status"])
            
            exists_on_disk = (team, uid) in existing_set
            
            if current_status == "completed" and not exists_on_disk:
                connection.execute(
                    "UPDATE upload_sources SET status = 'deleted', updated_at = ? WHERE upload_id = ?",
                    (now, uid),
                )
                updated_upload_ids.append(uid)
            elif current_status == "sources_removed" and exists_on_disk:
                connection.execute(
                    "UPDATE upload_sources SET status = 'completed', updated_at = ? WHERE upload_id = ?",
                    (now, uid),
                )
                updated_upload_ids.append(uid)
                
    for uid in updated_upload_ids:
        refresh_upload_aggregate(uid)

