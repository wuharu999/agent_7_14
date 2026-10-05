"""Durable email outbox in the existing gateway database; no network operations."""
from __future__ import annotations

import uuid
from datetime import datetime

from ecs.app import database


def initialize() -> None:
    with database._DB_LOCK, database._connect() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS email_outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_key TEXT NOT NULL,
                kind TEXT NOT NULL,
                user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                recipient TEXT NOT NULL,
                subject TEXT NOT NULL,
                body TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                available_at REAL NOT NULL,
                lease_token TEXT,
                last_error TEXT,
                created_at REAL NOT NULL,
                sent_at REAL,
                UNIQUE(event_key, recipient)
            );
            CREATE INDEX IF NOT EXISTS idx_email_outbox_due
                ON email_outbox(status, available_at);
        """)


def admins() -> list[dict]:
    with database._DB_LOCK, database._connect() as db:
        return [dict(row) for row in db.execute(
            "SELECT id, email FROM users WHERE role='admin' AND is_active=1 AND email IS NOT NULL"
        )]


def enqueue(event_key: str, kind: str, user: dict, subject: str, body: str, now: float) -> None:
    with database._DB_LOCK, database._connect() as db:
        db.execute("""INSERT OR IGNORE INTO email_outbox
            (event_key, kind, user_id, recipient, subject, body, available_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (event_key, kind, user['id'], user['email'], subject, body, now, now))


def claim(now: float) -> dict | None:
    with database._DB_LOCK, database._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        # Recover interrupted sends, but retain the overall attempt bound.
        db.execute("""UPDATE email_outbox SET status='failed', lease_token=NULL,
            last_error='attempt_limit' WHERE status='sending' AND available_at<=? AND attempts>=5""", (now,))
        row = db.execute("""SELECT * FROM email_outbox WHERE
            status IN ('pending', 'sending') AND available_at<=? AND attempts<5
            ORDER BY id LIMIT 1""", (now,)).fetchone()
        if row is None:
            return None
        token = uuid.uuid4().hex
        db.execute("""UPDATE email_outbox SET status='sending', attempts=attempts+1,
            available_at=?, lease_token=? WHERE id=?""", (now + 600, token, row['id']))
        return dict(row) | {'attempts': row['attempts'] + 1, 'lease_token': token}


def recipient_active(item: dict) -> bool:
    with database._DB_LOCK, database._connect() as db:
        return db.execute("""SELECT 1 FROM users WHERE id=? AND email=?
            AND role='admin' AND is_active=1""", (item['user_id'], item['recipient'])).fetchone() is not None


def finish(item: dict, now: float, *, error: str | None = None, permanent: bool = False,
           skipped: bool = False) -> None:
    if skipped:
        status = 'skipped'
    elif error:
        status = 'failed' if permanent or item['attempts'] >= 5 else 'pending'
    else:
        status = 'sent'
    delay = min(3600, 60 * 2 ** (item['attempts'] - 1))
    with database._DB_LOCK, database._connect() as db:
        db.execute("""UPDATE email_outbox SET status=?, available_at=?, sent_at=?,
            last_error=?, lease_token=NULL WHERE id=? AND lease_token=?""",
            (status, now + delay, now if status == 'sent' else None, error,
             item['id'], item['lease_token']))


def weekly_activity(start: datetime, end: datetime) -> dict:
    bounds = (start.isoformat(), end.isoformat())
    with database._DB_LOCK, database._connect() as db:
        qa = dict(db.execute("""SELECT COUNT(*) AS questions,
            COUNT(DISTINCT conversation_id) AS conversations,
            COUNT(DISTINCT ip_address) AS client_ips FROM qa_question_records
            WHERE asked_at>=? AND asked_at<?""", bounds).fetchone())
        uploads = dict(db.execute("""SELECT COUNT(*) AS uploads,
            COALESCE(SUM(size_bytes),0) AS upload_bytes FROM uploads
            WHERE created_at>=? AND created_at<?""", bounds).fetchone())
        actions = [dict(row) for row in db.execute("""SELECT action, COUNT(*) AS count
            FROM file_audit_log WHERE created_at>=? AND created_at<? GROUP BY action ORDER BY action""", bounds)]
        users = [dict(row) for row in db.execute("""SELECT username, COUNT(*) AS actions,
            SUM(CASE WHEN action='upload_source' THEN 1 ELSE 0 END) AS uploads
            FROM file_audit_log WHERE created_at>=? AND created_at<?
            GROUP BY username ORDER BY actions DESC, username LIMIT 20""", bounds)]
        active_users = db.execute("""SELECT COUNT(DISTINCT user_id) FROM file_audit_log
            WHERE created_at>=? AND created_at<? AND user_id IS NOT NULL""", bounds).fetchone()[0]
        contradictions = db.execute("""SELECT COUNT(*) FROM wiki_contradictions
            WHERE created_at>=? AND created_at<?""", bounds).fetchone()[0]
        warnings = db.execute("""SELECT COUNT(*) FROM upload_security_warnings
            WHERE created_at>=? AND created_at<?""", bounds).fetchone()[0]
    return qa | uploads | {'actions': actions, 'users': users, 'active_users': active_users,
                           'contradictions': contradictions, 'warnings': warnings}
