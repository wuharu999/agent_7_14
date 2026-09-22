"""Account-owned transcripts in the gateway's existing SQLite database."""
from __future__ import annotations

import json
import uuid
from typing import Any

from ecs.app import database as db


def list_conversations(user_id: int, *, limit: int = 30, offset: int = 0) -> dict:
    with db._DB_LOCK, db._connect() as connection:
        rows = connection.execute(
            "SELECT id, title, team, language, created_at, updated_at FROM chat_conversations "
            "WHERE user_id = ? ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?",
            (user_id, limit + 1, offset),
        ).fetchall()
    return {"conversations": [dict(row) for row in rows[:limit]], "has_more": len(rows) > limit}


def get_conversation(user_id: int, conversation_id: str, *, before: int | None = None,
                     limit: int = 30) -> dict | None:
    with db._DB_LOCK, db._connect() as connection:
        row = connection.execute(
            "SELECT id, title, team, language, created_at, updated_at FROM chat_conversations "
            "WHERE id = ? AND user_id = ?", (conversation_id, user_id),
        ).fetchone()
        if row is None:
            return None
        rows = connection.execute(
            "SELECT * FROM chat_turns WHERE conversation_id = ? AND (? IS NULL OR id < ?) "
            "ORDER BY id DESC LIMIT ?", (conversation_id, before, before, limit + 1),
        ).fetchall()
    turns = [dict(turn) for turn in reversed(rows[:limit])]
    for turn in turns:
        turn["images"] = json.loads(turn["images"])
    return {**dict(row), "turns": turns, "has_more": len(rows) > limit}


def begin_turn(user_id: int, conversation_id: str, question: str, team: str,
               language: str) -> tuple[str, int, list[dict[str, str]]]:
    """Commit the question before inference. Only the owner can continue a chat."""
    now = db.utc_now()
    with db._DB_LOCK, db._connect() as connection:
        if conversation_id:
            owner = connection.execute(
                "SELECT user_id FROM chat_conversations WHERE id = ?", (conversation_id,),
            ).fetchone()
            if owner is None or owner["user_id"] != user_id:
                raise LookupError("Conversation not found")
        else:
            conversation_id = "chat:" + uuid.uuid4().hex
            connection.execute(
                "INSERT INTO chat_conversations (id, user_id, title, team, language, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (conversation_id, user_id, question[:100], team, language, now, now),
            )
        rows = connection.execute(
            "SELECT question, answer FROM chat_turns WHERE conversation_id = ? "
            "AND status IN ('complete', 'interrupted') "
            "ORDER BY id DESC LIMIT 6", (conversation_id,),
        ).fetchall()
        history = []
        for row in reversed(rows):
            history.extend([{"role": "user", "content": row["question"]},
                            {"role": "bot", "content": row["answer"]}])
        cursor = connection.execute(
            "INSERT INTO chat_turns (conversation_id, question, team, language, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'streaming', ?, ?)",
            (conversation_id, question, team, language, now, now),
        )
        connection.execute(
            "UPDATE chat_conversations SET team = ?, language = ?, updated_at = ? WHERE id = ?",
            (team, language, now, conversation_id),
        )
    return conversation_id, int(cursor.lastrowid), history


def finish_turn(user_id: int, turn_id: int, answer: str, images: list[dict[str, Any]], status: str) -> None:
    now = db.utc_now()
    with db._DB_LOCK, db._connect() as connection:
        connection.execute(
            "UPDATE chat_turns SET answer = ?, images = ?, status = ?, updated_at = ? "
            "WHERE id = ? AND conversation_id IN (SELECT id FROM chat_conversations WHERE user_id = ?)",
            (answer, json.dumps(images, ensure_ascii=False), status, now, turn_id, user_id),
        )
        connection.execute(
            "UPDATE chat_conversations SET updated_at = ? WHERE user_id = ? "
            "AND id = (SELECT conversation_id FROM chat_turns WHERE id = ?)", (now, user_id, turn_id),
        )


def interrupt_unfinished_turns() -> None:
    """A gateway restart cannot resume an old HTTP response."""
    with db._DB_LOCK, db._connect() as connection:
        connection.execute("UPDATE chat_turns SET status = 'interrupted' WHERE status = 'streaming'")


def import_conversation(user_id: int, legacy_id: str, team: str, language: str,
                        messages: list[dict[str, str]]) -> str:
    """Import one explicitly selected browser transcript, once per account."""
    now = db.utc_now()
    with db._DB_LOCK, db._connect() as connection:
        existing = connection.execute(
            "SELECT id FROM chat_conversations WHERE user_id = ? AND imported_from = ?",
            (user_id, legacy_id),
        ).fetchone()
        if existing:
            return str(existing["id"])
        conversation_id = "chat:" + uuid.uuid4().hex
        connection.execute(
            "INSERT INTO chat_conversations (id, user_id, title, team, language, imported_from, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (conversation_id, user_id, messages[0]["content"][:100], team, language, legacy_id, now, now),
        )
        for index in range(0, len(messages), 2):
            answer = messages[index + 1]["content"] if index + 1 < len(messages) else ""
            connection.execute(
                "INSERT INTO chat_turns (conversation_id, question, answer, team, language, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (conversation_id, messages[index]["content"], answer, team, language,
                 "complete" if answer else "interrupted", now, now),
            )
    return conversation_id
