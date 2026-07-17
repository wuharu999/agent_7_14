from __future__ import annotations

import re
import uuid

import time
from collections import defaultdict

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ecs.app.gateway import gateway
from ecs.app.languages import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES

router = APIRouter()
_CONVERSATION_ID = re.compile(r"^[A-Za-z0-9:_-]{1,128}$")

class RateLimiter:
    def __init__(self):
        self.history = defaultdict(list)

    def is_allowed(self, client_id: str) -> tuple[bool, str]:
        now = time.time()
        # Clean up old entries
        self.history[client_id] = [ts for ts in self.history[client_id] if now - ts < 3600]
        timestamps = self.history[client_id]

        # 50 per hour
        if len(timestamps) >= 50:
            return False, "Rate limit exceeded: 50 questions per hour."
        
        # 10 per minute
        recent = [ts for ts in timestamps if now - ts < 60]
        if len(recent) >= 10:
            return False, "Rate limit exceeded: 10 questions per minute."
        
        self.history[client_id].append(now)
        return True, ""

limiter = RateLimiter()

@router.post("/ask")
async def ask(request: Request, body: dict):
    client_ip = request.client.host if request.client else "unknown"
    allowed, msg = limiter.is_allowed(client_ip)
    if not allowed:
        return JSONResponse({"error": msg}, status_code=429)
    question = str(body.get("question") or "").strip()
    if not question:
        return JSONResponse({"error": "Question cannot be empty"}, status_code=400)
    if len(question) > 20_000:
        return JSONResponse({"error": "Question is too long"}, status_code=400)

    language = str(body.get("language") or DEFAULT_LANGUAGE)
    if language not in SUPPORTED_LANGUAGES:
        return JSONResponse({"error": "Unsupported answer language"}, status_code=400)

    team = str(body.get("team") or "").strip()
    if not team:
        return JSONResponse({"error": "Team cannot be empty"}, status_code=400)

    conversation_id = str(body.get("conversation_id") or "").strip()
    if not _CONVERSATION_ID.fullmatch(conversation_id):
        conversation_id = f"web:{uuid.uuid4().hex}"

    try:
        answer = await gateway.ask(
            question,
            team=team,
            conversation_id=conversation_id,
            language=language,
        )
        return {
            "answer": answer,
            "conversation_id": conversation_id,
            "language": language,
            "team": team,
        }
    except TimeoutError as exc:
        return JSONResponse({"error": str(exc)}, status_code=504)
    except ConnectionError as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)
