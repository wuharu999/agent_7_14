from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from shared.team_names import normalize_team_name

from ecs.app.config import ALLOWED_TEAMS, DATABASE_PATH

_DB_LOCK = threading.RLock()
_ROBOTS_SOURCE_TREE_SYNCED_KEY = "robots_source_tree_synced"


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
                role TEXT NOT NULL CHECK(role IN ('editor', 'admin')),
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

            CREATE TABLE IF NOT EXISTS scenario_assessments (
                assessment_id TEXT PRIMARY KEY,
                user_id INTEGER,
                conversation_id TEXT NOT NULL DEFAULT '',
                model_id TEXT NOT NULL,
                scenario_spec TEXT NOT NULL,
                atomic_requirements TEXT NOT NULL DEFAULT '[]',
                capabilities TEXT NOT NULL DEFAULT '[]',
                feasibility_assessment TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS capability_draft_stubs (
                stub_id TEXT PRIMARY KEY,
                assessment_id TEXT NOT NULL,
                requirement_id TEXT NOT NULL,
                model_id TEXT NOT NULL,
                name TEXT NOT NULL,
                details TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'draft',
                created_by INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(assessment_id, requirement_id),
                FOREIGN KEY(assessment_id) REFERENCES scenario_assessments(assessment_id) ON DELETE CASCADE,
                FOREIGN KEY(created_by) REFERENCES users(id) ON DELETE RESTRICT
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
            CREATE INDEX IF NOT EXISTS idx_scenario_assessments_created
                ON scenario_assessments(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_scenario_assessments_model
                ON scenario_assessments(model_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_capability_stubs_created
                ON capability_draft_stubs(created_at DESC);
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
        scenario_columns = _columns(connection, "scenario_assessments")
        if "conversation_id" not in scenario_columns:
            connection.execute(
                "ALTER TABLE scenario_assessments ADD COLUMN conversation_id TEXT NOT NULL DEFAULT ''"
            )
        if "atomic_requirements" not in scenario_columns:
            connection.execute(
                "ALTER TABLE scenario_assessments ADD COLUMN atomic_requirements TEXT NOT NULL DEFAULT '[]'"
            )
        if "capabilities" not in scenario_columns:
            connection.execute(
                "ALTER TABLE scenario_assessments ADD COLUMN capabilities TEXT NOT NULL DEFAULT '[]'"
            )

        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS qa_visitors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ip_address TEXT NOT NULL,
                user_id INTEGER,
                visited_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS robots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                display_name_en TEXT NOT NULL DEFAULT '',
                display_name_zh TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                storage_path TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS robot_editors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                robot_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                assigned_at TEXT NOT NULL,
                UNIQUE(robot_id, user_id),
                FOREIGN KEY(robot_id) REFERENCES robots(id) ON DELETE CASCADE,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS wiki_contradictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                team TEXT NOT NULL,
                details TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        if "display_name_en" not in _columns(connection, "robots"):
            connection.execute(
                "ALTER TABLE robots ADD COLUMN display_name_en TEXT NOT NULL DEFAULT ''"
            )
        if "display_name_zh" not in _columns(connection, "robots"):
            connection.execute(
                "ALTER TABLE robots ADD COLUMN display_name_zh TEXT NOT NULL DEFAULT ''"
            )
        connection.execute("UPDATE robots SET display_name_en = name WHERE display_name_en = ''")
        connection.execute("UPDATE robots SET display_name_zh = name WHERE display_name_zh = ''")

        now = utc_now()
        source_tree_synced = connection.execute(
            "SELECT value FROM app_settings WHERE key = ?",
            (_ROBOTS_SOURCE_TREE_SYNCED_KEY,),
        ).fetchone()
        if source_tree_synced is None or source_tree_synced["value"] != "1":
            # Preserve legacy robot metadata only until the first successful
            # Worker source-tree refresh establishes the authoritative folders.
            teams_to_migrate = set(ALLOWED_TEAMS)
            for row in connection.execute("SELECT teams FROM users").fetchall():
                for t in row["teams"].split(","):
                    if t.strip():
                        teams_to_migrate.add(t.strip())
            for row in connection.execute("SELECT DISTINCT team FROM uploads").fetchall():
                if row["team"].strip():
                    teams_to_migrate.add(row["team"].strip())

            for t in teams_to_migrate:
                connection.execute(
                    "INSERT OR IGNORE INTO robots (name, description, storage_path, created_at) VALUES (?, ?, ?, ?)",
                    (t, f"Legacy team {t}", t, now),
                )

        # Seed default admin account if no admin exists
        admin_exists = connection.execute(
            "SELECT 1 FROM users WHERE role = 'admin' LIMIT 1"
        ).fetchone()
        if not admin_exists:
            import os
            from ecs.app.auth import hash_password
            default_pw = os.environ.get("DEFAULT_ADMIN_PASSWORD", "Admin#2026!Secured89")
            pw_hash, pw_salt = hash_password(default_pw)
            all_teams = os.environ.get("ALLOWED_TEAMS", "tian_gong,walker_s2,walker_c1")
            connection.execute(
                """
                INSERT INTO users (
                    username, email, password_hash, password_salt, role, teams,
                    is_active, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'admin', ?, 1, ?, ?)
                """,
                ("admin", "admin@localhost", pw_hash, pw_salt, all_teams, now, now),
            )
            logging.getLogger(__name__).info("Seeded default admin account (username: admin)")


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
        user_id = int(cursor.lastrowid)

        if teams.strip():
            for t in teams.split(","):
                t = t.strip()
                if t:
                    connection.execute(
                        "INSERT OR IGNORE INTO robots (name, description, storage_path, created_at) VALUES (?, ?, ?, ?)",
                        (t, f"Team {t}", t, now)
                    )
                    robot_id = connection.execute("SELECT id FROM robots WHERE name = ?", (t,)).fetchone()["id"]
                    connection.execute(
                        "INSERT OR IGNORE INTO robot_editors (robot_id, user_id, assigned_at) VALUES (?, ?, ?)",
                        (robot_id, user_id, now)
                    )
        return user_id


def update_user_record(
    user_id: int, email: str, password_hash: str, password_salt: str, role: str, teams: str = ""
) -> None:
    now = utc_now()
    with _DB_LOCK, _connect() as connection:
        connection.execute(
            "UPDATE users SET email = ?, password_hash = ?, password_salt = ?, role = ?, teams = ?, "
            "is_active = 1, updated_at = ? WHERE id = ?",
            (email, password_hash, password_salt, role, teams, now, user_id),
        )
        # Clear existing associations first
        connection.execute("DELETE FROM robot_editors WHERE user_id = ?", (user_id,))
        if teams.strip():
            for t in teams.split(","):
                t = t.strip()
                if t:
                    connection.execute(
                        "INSERT OR IGNORE INTO robots (name, description, storage_path, created_at) VALUES (?, ?, ?, ?)",
                        (t, f"Team {t}", t, now)
                    )
                    robot_id = connection.execute("SELECT id FROM robots WHERE name = ?", (t,)).fetchone()["id"]
                    connection.execute(
                        "INSERT OR IGNORE INTO robot_editors (robot_id, user_id, assigned_at) VALUES (?, ?, ?)",
                        (robot_id, user_id, now)
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
            "SELECT id, username, email, role, teams, is_active, created_at, updated_at FROM users ORDER BY username"
        ).fetchall()
    return [dict(row) for row in rows]


def update_user_details(user_id: int, role: str, teams: str = "") -> None:
    now = utc_now()
    with _DB_LOCK, _connect() as connection:
        connection.execute(
            "UPDATE users SET role = ?, teams = ?, updated_at = ? WHERE id = ?",
            (role, teams, now, user_id),
        )
        connection.execute("DELETE FROM robot_editors WHERE user_id = ?", (user_id,))
        if teams.strip():
            for t in teams.split(","):
                t = t.strip()
                if t:
                    connection.execute(
                        "INSERT OR IGNORE INTO robots (name, description, storage_path, created_at) VALUES (?, ?, ?, ?)",
                        (t, f"Team {t}", t, now)
                    )
                    robot_row = connection.execute("SELECT id FROM robots WHERE name = ?", (t,)).fetchone()
                    if robot_row:
                        robot_id = robot_row["id"]
                        connection.execute(
                            "INSERT OR IGNORE INTO robot_editors (robot_id, user_id, assigned_at) VALUES (?, ?, ?)",
                            (robot_id, user_id, now)
                        )


def update_user_password(user_id: int, password_hash: str, password_salt: str) -> None:
    now = utc_now()
    with _DB_LOCK, _connect() as connection:
        connection.execute(
            "UPDATE users SET password_hash = ?, password_salt = ?, updated_at = ? WHERE id = ?",
            (password_hash, password_salt, now, user_id),
        )


def update_user_email(user_id: int, email: str) -> None:
    now = utc_now()
    with _DB_LOCK, _connect() as connection:
        try:
            connection.execute(
                "UPDATE users SET email = ?, updated_at = ? WHERE id = ?",
                (email, now, user_id),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("Email already registered") from exc


def toggle_user_active(user_id: int) -> bool:
    now = utc_now()
    with _DB_LOCK, _connect() as connection:
        row = connection.execute("SELECT is_active FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            raise ValueError("User not found")
        new_state = 0 if bool(row["is_active"]) else 1
        connection.execute(
            "UPDATE users SET is_active = ?, updated_at = ? WHERE id = ?",
            (new_state, now, user_id),
        )
        return bool(new_state)


def delete_disabled_user(user_id: int) -> dict[str, Any]:
    """Permanently remove an inactive account and its dependent session data."""
    with _DB_LOCK, _connect() as connection:
        row = connection.execute(
            "SELECT id, username, role, is_active FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if not row:
            raise ValueError("User not found")
        if bool(row["is_active"]):
            raise ValueError("Disable the account before removing it")
        if row["role"] == "admin":
            admin_count = connection.execute(
                "SELECT COUNT(*) AS count FROM users WHERE role = 'admin'"
            ).fetchone()
            if int(admin_count["count"]) <= 1:
                raise ValueError("Cannot remove the last admin account")
        connection.execute("DELETE FROM users WHERE id = ?", (user_id,))
    return {"id": int(row["id"]), "username": str(row["username"])}


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
                u.email,
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


def get_all_robots() -> list[dict[str, Any]]:
    with _DB_LOCK, _connect() as connection:
        rows = connection.execute("SELECT * FROM robots ORDER BY name").fetchall()
    return [dict(row) for row in rows]


def get_robot_by_name(name: str) -> dict[str, Any] | None:
    with _DB_LOCK, _connect() as connection:
        row = connection.execute(
            "SELECT * FROM robots WHERE name = ?", (name,)
        ).fetchone()
    return dict(row) if row else None


def create_robot(
    name: str,
    description: str = "",
    storage_path: str = "",
    *,
    display_name_en: str | None = None,
    display_name_zh: str | None = None,
) -> int:
    name = normalize_team_name(name, allow_reserved=False)
    normalized_storage_path = normalize_team_name(
        storage_path or name, allow_reserved=False
    )
    if normalized_storage_path != name:
        raise ValueError("Robot storage path must match the robot name")
    now = utc_now()
    with _DB_LOCK, _connect() as connection:
        try:
            cursor = connection.execute(
                "INSERT INTO robots (name, display_name_en, display_name_zh, description, storage_path, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (name, (display_name_en or name).strip(), (display_name_zh or name).strip(), description.strip(), normalized_storage_path, now)
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"Robot '{name}' already exists") from exc
        return int(cursor.lastrowid)


def get_robot_by_id(robot_id: int) -> dict[str, Any] | None:
    with _DB_LOCK, _connect() as connection:
        row = connection.execute(
            "SELECT * FROM robots WHERE id = ?", (robot_id,)
        ).fetchone()
    return dict(row) if row else None


def update_robot_display_names(
    robot_id: int,
    *,
    display_name_en: str,
    display_name_zh: str,
) -> dict[str, Any]:
    """Update display names without changing the stable Worker folder key."""
    english_name = display_name_en.strip()
    chinese_name = display_name_zh.strip()
    if not english_name or not chinese_name:
        raise ValueError("English and Chinese robot names are required")
    if len(english_name) > 64 or len(chinese_name) > 64:
        raise ValueError("Robot display names must be 64 characters or fewer")
    with _DB_LOCK, _connect() as connection:
        cursor = connection.execute(
            "UPDATE robots SET display_name_en = ?, display_name_zh = ? WHERE id = ?",
            (english_name, chinese_name, robot_id),
        )
        if cursor.rowcount != 1:
            raise ValueError("Robot not found")
    robot = get_robot_by_id(robot_id)
    assert robot is not None
    return robot


def list_active_editors() -> list[dict[str, Any]]:
    with _DB_LOCK, _connect() as connection:
        rows = connection.execute(
            "SELECT id, username, email FROM users "
            "WHERE role = 'editor' AND is_active = 1 ORDER BY username"
        ).fetchall()
    return [dict(row) for row in rows]


def _sync_user_team_names(connection: sqlite3.Connection, user_id: int) -> None:
    names = [
        str(row["name"])
        for row in connection.execute(
            "SELECT r.name FROM robots r "
            "JOIN robot_editors re ON re.robot_id = r.id "
            "WHERE re.user_id = ? ORDER BY r.name",
            (user_id,),
        ).fetchall()
    ]
    connection.execute(
        "UPDATE users SET teams = ?, updated_at = ? WHERE id = ?",
        (",".join(names), utc_now(), user_id),
    )


def _mark_robot_source_tree_synced(connection: sqlite3.Connection) -> None:
    now = utc_now()
    connection.execute(
        """
        INSERT INTO app_settings (key, value, updated_at)
        VALUES (?, '1', ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value,
            updated_at = excluded.updated_at
        """,
        (_ROBOTS_SOURCE_TREE_SYNCED_KEY, now),
    )


def robots_source_tree_synced() -> bool:
    with _DB_LOCK, _connect() as connection:
        row = connection.execute(
            "SELECT value FROM app_settings WHERE key = ?",
            (_ROBOTS_SOURCE_TREE_SYNCED_KEY,),
        ).fetchone()
    return row is not None and str(row["value"]) == "1"


def reconcile_robots_with_source_tree(robot_names: list[str]) -> dict[str, list[str]]:
    """Make robot metadata exactly match Worker raw/sources folders."""
    normalized_names = sorted(
        {
            normalize_team_name(str(name), allow_reserved=False)
            for name in robot_names
        }
    )
    desired = set(normalized_names)
    now = utc_now()

    with _DB_LOCK, _connect() as connection:
        existing_rows = connection.execute(
            "SELECT id, name FROM robots ORDER BY name"
        ).fetchall()
        existing = {str(row["name"]): int(row["id"]) for row in existing_rows}
        added = sorted(desired - set(existing))
        removed = sorted(set(existing) - desired)

        for name in added:
            connection.execute(
                """
                INSERT INTO robots (name, description, storage_path, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (name, "Discovered from Worker source tree", name, now),
            )

        affected_editor_ids: set[int] = set()
        if removed:
            placeholders = ",".join("?" for _ in removed)
            rows = connection.execute(
                "SELECT DISTINCT re.user_id FROM robot_editors re "
                "JOIN robots r ON r.id = re.robot_id "
                f"WHERE r.name IN ({placeholders})",
                removed,
            ).fetchall()
            affected_editor_ids = {int(row["user_id"]) for row in rows}
            connection.execute(
                f"DELETE FROM robots WHERE name IN ({placeholders})",
                removed,
            )

        for user_id in affected_editor_ids:
            _sync_user_team_names(connection, user_id)
        _mark_robot_source_tree_synced(connection)

    return {
        "robots": normalized_names,
        "added": added,
        "removed": removed,
    }


def delete_robot(robot_id: int) -> dict[str, Any] | None:
    with _DB_LOCK, _connect() as connection:
        row = connection.execute(
            "SELECT * FROM robots WHERE id = ?", (robot_id,)
        ).fetchone()
        if row is None:
            return None
        editor_rows = connection.execute(
            "SELECT user_id FROM robot_editors WHERE robot_id = ?",
            (robot_id,),
        ).fetchall()
        connection.execute("DELETE FROM robots WHERE id = ?", (robot_id,))
        for editor_row in editor_rows:
            _sync_user_team_names(connection, int(editor_row["user_id"]))
        _mark_robot_source_tree_synced(connection)
    return dict(row)


def assign_robot_editor(robot_id: int, user_id: int) -> None:
    now = utc_now()
    with _DB_LOCK, _connect() as connection:
        robot = connection.execute(
            "SELECT id FROM robots WHERE id = ?", (robot_id,)
        ).fetchone()
        if robot is None:
            raise ValueError("Robot not found")
        user = connection.execute(
            "SELECT role, is_active FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if user is None:
            raise ValueError("User not found")
        if str(user["role"]) != "editor":
            raise ValueError("Only editor accounts can be assigned to robots")
        if not bool(user["is_active"]):
            raise ValueError("Inactive editors cannot be assigned to robots")
        connection.execute(
            "INSERT OR IGNORE INTO robot_editors (robot_id, user_id, assigned_at) VALUES (?, ?, ?)",
            (robot_id, user_id, now),
        )
        _sync_user_team_names(connection, user_id)


def remove_robot_editor(robot_id: int, user_id: int) -> None:
    with _DB_LOCK, _connect() as connection:
        connection.execute(
            "DELETE FROM robot_editors WHERE robot_id = ? AND user_id = ?",
            (robot_id, user_id)
        )
        _sync_user_team_names(connection, user_id)


def get_user_robots(user_id: int) -> list[dict[str, Any]]:
    with _DB_LOCK, _connect() as connection:
        rows = connection.execute(
            """
            SELECT r.* FROM robots r
            JOIN robot_editors re ON r.id = re.robot_id
            WHERE re.user_id = ?
            ORDER BY r.name
            """, (user_id,)
        ).fetchall()
    return [dict(row) for row in rows]


def get_robot_editors(robot_id: int) -> list[dict[str, Any]]:
    with _DB_LOCK, _connect() as connection:
        rows = connection.execute(
            """
            SELECT u.id, u.username, u.email, re.assigned_at
            FROM users u
            JOIN robot_editors re ON u.id = re.user_id
            WHERE re.robot_id = ?
            ORDER BY u.username
            """, (robot_id,)
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


def list_recent_uploads_with_sources(hours: int = 24, limit: int = 200) -> list[dict[str, Any]]:
    """Return uploads created within the requested retention window.

    Source rows are included so the browser can keep failed filenames and
    ingestion errors visible for the requested retention window.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    with _DB_LOCK, _connect() as connection:
        upload_rows = connection.execute(
            """
            SELECT upload_id, team, filename, size_bytes, status, stage, message,
                   percent, error, created_by, created_at, updated_at
            FROM uploads
            WHERE created_at >= ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (cutoff, limit),
        ).fetchall()
        upload_ids = [str(row["upload_id"]) for row in upload_rows]
        source_rows = []
        if upload_ids:
            source_placeholders = ",".join("?" for _ in upload_ids)
            source_rows = connection.execute(
                f"""
                SELECT upload_id, source_identity, status, error, files_written,
                       retry_count, max_retries, active_queue_count, updated_at
                FROM upload_sources
                WHERE upload_id IN ({source_placeholders})
                ORDER BY source_identity
                """,
                upload_ids,
            ).fetchall()

    sources_by_upload: dict[str, list[dict[str, Any]]] = {upload_id: [] for upload_id in upload_ids}
    for row in source_rows:
        source = dict(row)
        try:
            source["files_written"] = json.loads(source.get("files_written") or "[]")
        except json.JSONDecodeError:
            source["files_written"] = []
        sources_by_upload.setdefault(str(source["upload_id"]), []).append(source)

    uploads: list[dict[str, Any]] = []
    for row in upload_rows:
        upload = dict(row)
        upload["sources"] = sources_by_upload.get(str(upload["upload_id"]), [])
        uploads.append(upload)
    return uploads


def get_recent_llm_wiki_source_counts(hours: int = 24) -> dict[str, int]:
    """Count each recently uploaded source once by its persisted ingestion state.

    LLM Wiki's queue can contain multiple entries for one source (including
    retries), and it does not contain sources still waiting for Source Watch.
    The upload-source table is therefore the authoritative status scope for
    the web summary.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    counts = {
        "total": 0,
        "waiting": 0,
        "queued": 0,
        "processing": 0,
        "retrying": 0,
        "completed": 0,
        "failed": 0,
        "deleted": 0,
    }
    with _DB_LOCK, _connect() as connection:
        rows = connection.execute(
            """
            SELECT s.status, s.error, s.retry_count, s.max_retries
            FROM upload_sources AS s
            INNER JOIN uploads AS u ON u.upload_id = s.upload_id
            WHERE u.created_at >= ?
            """,
            (cutoff,),
        ).fetchall()

    for row in rows:
        counts["total"] += 1
        status = str(row["status"] or "waiting")
        retry_count = int(row["retry_count"] or 0)
        max_retries = int(row["max_retries"] or 0)
        retry_available = retry_count > 0 and (
            max_retries <= 0 or retry_count < max_retries
        )

        if status == "completed":
            counts["completed"] += 1
        elif status == "processing":
            counts["processing"] += 1
        elif status == "retrying" or (status == "failed" and retry_available):
            counts["retrying"] += 1
        elif status == "queued":
            counts["queued"] += 1
        elif status == "failed":
            counts["failed"] += 1
        elif status == "deleted":
            counts["deleted"] += 1
        else:
            counts["waiting"] += 1
    return counts


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


def add_wiki_contradiction(team: str, details: str) -> None:
    now = utc_now()
    with _DB_LOCK, _connect() as connection:
        connection.execute(
            "INSERT INTO wiki_contradictions (team, details, created_at) VALUES (?, ?, ?)",
            (team, details, now),
        )


def get_recent_wiki_contradictions(days: int = 7) -> list[dict[str, Any]]:
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with _DB_LOCK, _connect() as connection:
        rows = connection.execute(
            "SELECT * FROM wiki_contradictions WHERE created_at >= ? ORDER BY created_at DESC",
            (cutoff,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_allowed_teams() -> list[str]:
    database_names: list[str] = []
    try:
        for robot in get_all_robots():
            name = str(robot.get("name") or "")
            if name and name not in database_names:
                database_names.append(name)
    except Exception:
        return list(ALLOWED_TEAMS)

    if robots_source_tree_synced():
        return database_names

    names = list(ALLOWED_TEAMS)
    for name in database_names:
        if name not in names:
            names.append(name)
    return names


def get_robot_options() -> list[dict[str, str]]:
    """Return stable Worker folder keys with browser display names."""
    return [
        {
            "name": str(robot["name"]),
            "english_name": str(robot.get("display_name_en") or robot["name"]),
            "chinese_name": str(robot.get("display_name_zh") or robot["name"]),
        }
        for robot in get_all_robots()
    ]


# ---------------------------------------------------------------------------
# Scenario feasibility assessments and R&D gap drafts
# ---------------------------------------------------------------------------

def create_scenario_assessment(
    *,
    assessment_id: str,
    user_id: int | None,
    conversation_id: str,
    model_id: str,
    scenario_spec: dict[str, Any],
    atomic_requirements: list[dict[str, Any]],
    capabilities: list[dict[str, Any]],
    feasibility_assessment: dict[str, Any],
) -> None:
    with _DB_LOCK, _connect() as connection:
        connection.execute(
            """
            INSERT INTO scenario_assessments (
                assessment_id, user_id, conversation_id, model_id,
                scenario_spec, atomic_requirements, capabilities,
                feasibility_assessment, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                assessment_id,
                user_id,
                conversation_id,
                model_id,
                json.dumps(scenario_spec, ensure_ascii=False),
                json.dumps(atomic_requirements, ensure_ascii=False),
                json.dumps(capabilities, ensure_ascii=False),
                json.dumps(feasibility_assessment, ensure_ascii=False),
                utc_now(),
            ),
        )


def _assessment_from_row(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    for field, fallback in (
        ("scenario_spec", {}),
        ("atomic_requirements", []),
        ("capabilities", []),
        ("feasibility_assessment", {}),
    ):
        try:
            result[field] = json.loads(str(result.get(field) or ""))
        except (TypeError, ValueError, json.JSONDecodeError):
            result[field] = fallback
    return result


def get_scenario_assessment(assessment_id: str) -> dict[str, Any] | None:
    with _DB_LOCK, _connect() as connection:
        row = connection.execute(
            "SELECT * FROM scenario_assessments WHERE assessment_id = ?",
            (assessment_id,),
        ).fetchone()
    return _assessment_from_row(row) if row is not None else None


def list_scenario_assessments(limit: int = 100) -> list[dict[str, Any]]:
    bounded_limit = max(1, min(int(limit), 500))
    with _DB_LOCK, _connect() as connection:
        rows = connection.execute(
            "SELECT * FROM scenario_assessments ORDER BY created_at DESC LIMIT ?",
            (bounded_limit,),
        ).fetchall()
    return [_assessment_from_row(row) for row in rows]


def aggregate_capability_gaps(limit: int = 100) -> list[dict[str, Any]]:
    aggregated: dict[tuple[str, str], dict[str, Any]] = {}
    for assessment in list_scenario_assessments(limit=500):
        requirements = {
            str(item.get("requirement_id") or ""): item
            for item in assessment["atomic_requirements"]
            if isinstance(item, dict)
        }
        feasibility = assessment["feasibility_assessment"]
        for match in feasibility.get("matches", []):
            if not isinstance(match, dict):
                continue
            requirement_id = str(match.get("requirement_id") or "")
            rd_gap = match.get("rd_gap")
            gaps = [str(value) for value in match.get("gaps", []) if str(value)]
            if not isinstance(rd_gap, dict) and not gaps:
                continue
            requirement = requirements.get(requirement_id, {})
            name = str(requirement.get("name") or requirement_id or "Unknown gap")
            key = (str(assessment["model_id"]), name.casefold())
            item = aggregated.setdefault(
                key,
                {
                    "model_id": str(assessment["model_id"]),
                    "requirement_name": name,
                    "occurrence_count": 0,
                    "total_person_weeks": 0.0,
                    "domains": set(),
                    "latest_assessment_id": str(assessment["assessment_id"]),
                    "latest_requirement_id": requirement_id,
                    "latest_created_at": str(assessment["created_at"]),
                },
            )
            item["occurrence_count"] += 1
            if isinstance(rd_gap, dict):
                try:
                    person_weeks = float(rd_gap.get("person_weeks") or 0.0)
                except (TypeError, ValueError):
                    person_weeks = 0.0
                item["total_person_weeks"] += max(0.0, person_weeks)
                item["domains"].update(str(value) for value in rd_gap.get("domains", []))
    results: list[dict[str, Any]] = []
    for item in aggregated.values():
        count = int(item["occurrence_count"])
        item["average_person_weeks"] = round(float(item.pop("total_person_weeks")) / count, 2)
        item["domains"] = sorted(item["domains"])
        results.append(item)
    results.sort(key=lambda item: (-int(item["occurrence_count"]), str(item["requirement_name"])))
    return results[: max(1, min(int(limit), 500))]


def create_capability_draft_stub(
    *,
    stub_id: str,
    assessment_id: str,
    requirement_id: str,
    model_id: str,
    name: str,
    details: dict[str, Any],
    created_by: int,
) -> dict[str, Any]:
    with _DB_LOCK, _connect() as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO capability_draft_stubs (
                stub_id, assessment_id, requirement_id, model_id, name,
                details, status, created_by, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'draft', ?, ?)
            """,
            (
                stub_id,
                assessment_id,
                requirement_id,
                model_id,
                name,
                json.dumps(details, ensure_ascii=False),
                created_by,
                utc_now(),
            ),
        )
        row = connection.execute(
            """
            SELECT * FROM capability_draft_stubs
            WHERE assessment_id = ? AND requirement_id = ?
            """,
            (assessment_id, requirement_id),
        ).fetchone()
    if row is None:
        raise RuntimeError("Capability draft stub was not created")
    result = dict(row)
    result["details"] = json.loads(str(result["details"]))
    return result


def list_capability_draft_stubs(limit: int = 100) -> list[dict[str, Any]]:
    bounded_limit = max(1, min(int(limit), 500))
    with _DB_LOCK, _connect() as connection:
        rows = connection.execute(
            "SELECT * FROM capability_draft_stubs ORDER BY created_at DESC LIMIT ?",
            (bounded_limit,),
        ).fetchall()
    results = []
    for row in rows:
        item = dict(row)
        try:
            item["details"] = json.loads(str(item["details"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            item["details"] = {}
        results.append(item)
    return results
