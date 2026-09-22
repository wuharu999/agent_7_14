from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ecs.app import chat_history
from ecs.app.auth import require_user, verify_csrf
from ecs.app.languages import SUPPORTED_LANGUAGES

router = APIRouter(prefix="/api/conversations")


class ImportedMessage(BaseModel):
    role: Literal["user", "bot"]
    content: str = Field(max_length=200_000)


class BrowserConversation(BaseModel):
    legacy_id: str = Field(min_length=1, max_length=128)
    team: str = Field(min_length=1, max_length=128)
    language: str = Field(min_length=2, max_length=10)
    messages: list[ImportedMessage] = Field(min_length=1, max_length=120)


@router.get("")
async def list_history(request: Request, limit: int = Query(30, ge=1, le=100),
                       offset: int = Query(0, ge=0)):
    session = require_user(request)
    result = await asyncio.to_thread(chat_history.list_conversations, int(session["user_id"]),
                                     limit=limit, offset=offset)
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/import")
async def import_browser_history(payload: BrowserConversation, request: Request,
                                  x_csrf_token: str = Header(default="")):
    session = require_user(request)
    verify_csrf(session, x_csrf_token)
    messages = [message.model_dump() for message in payload.messages]
    if (payload.language not in SUPPORTED_LANGUAGES
            or sum(len(message["content"]) for message in messages) > 1_000_000
            or any(message["role"] != ("user" if index % 2 == 0 else "bot")
                   or (message["role"] == "user" and not message["content"].strip())
                   for index, message in enumerate(messages))):
        raise HTTPException(status_code=422, detail="Invalid browser conversation")
    conversation_id = await asyncio.to_thread(
        chat_history.import_conversation, int(session["user_id"]), payload.legacy_id,
        payload.team, payload.language, messages,
    )
    return JSONResponse({"id": conversation_id}, headers={"Cache-Control": "no-store"})


@router.get("/{conversation_id}")
async def read_history(conversation_id: str, request: Request,
                       before: int | None = Query(None, ge=1), limit: int = Query(30, ge=1, le=100)):
    session = require_user(request)
    result = await asyncio.to_thread(chat_history.get_conversation, int(session["user_id"]),
                                     conversation_id, before=before, limit=limit)
    if result is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return JSONResponse(result, headers={"Cache-Control": "no-store"})
