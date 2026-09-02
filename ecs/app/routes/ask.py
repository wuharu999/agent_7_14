from __future__ import annotations

import re
import uuid
import asyncio
import json
import logging

import time
from collections import defaultdict

from fastapi import APIRouter, Request, File, UploadFile, Header, BackgroundTasks
from fastapi.responses import JSONResponse, FileResponse
from pathlib import Path
from ecs.app.config import DATA_ROOT, WORKER_SHARED_SECRET
import shutil

from ecs.app.auth import require_roles
from ecs.app.database import record_qa_question
from ecs.app.gateway import gateway
from ecs.app.languages import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES

log = logging.getLogger(__name__)

router = APIRouter()
_CONVERSATION_ID = re.compile(r"^[A-Za-z0-9:_-]{1,128}$")
_CLIENT_HISTORY_MESSAGES = 12
_CLIENT_HISTORY_MESSAGE_CHARS = 8_000
_CLIENT_HISTORY_TOTAL_CHARS = 48_000
_STREAM_ERROR_MESSAGES = {
    "zh-CN": "暂时无法生成回答，请稍后再试。",
    "zh-TW": "暫時無法產生回答，請稍後再試。",
    "ko": "현재 답변을 생성할 수 없습니다. 잠시 후 다시 시도해 주세요.",
    "ja": "現在回答を生成できません。しばらくしてからもう一度お試しください。",
    "en": "Unable to answer right now. Please try again shortly.",
    "pt": "Não foi possível responder agora. Tente novamente em instantes.",
    "ru": "Сейчас не удаётся сформировать ответ. Повторите попытку позже.",
    "es": "No se puede responder ahora. Inténtalo de nuevo en breve.",
}


def _bounded_client_history(value: object) -> list[dict[str, str]]:
    """Keep only recent user/assistant display history within prompt limits."""
    if not isinstance(value, list):
        return []
    bounded_reversed: list[dict[str, str]] = []
    total = 0
    for item in reversed(value[-_CLIENT_HISTORY_MESSAGES:]):
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "")
        if role not in {"user", "bot"}:
            continue
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        remaining = _CLIENT_HISTORY_TOTAL_CHARS - total
        if remaining <= 0:
            break
        content = content[: min(_CLIENT_HISTORY_MESSAGE_CHARS, remaining)]
        bounded_reversed.append({"role": role, "content": content})
        total += len(content)
    return list(reversed(bounded_reversed))

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


def _sse_event(event_name: str, payload: dict) -> str:
    """Encode one browser event without exposing transport implementation."""
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event_name}\ndata: {data}\n\n"


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
    history = _bounded_client_history(body.get("history"))
    if team == "all":
        topic_label = "全部机器人"
    else:
        from ecs.app.database import get_robot_by_name

        robot = get_robot_by_name(team)
        topic_label = str((robot or {}).get("display_name_zh") or team)

    await asyncio.to_thread(
        record_qa_question,
        ip_address=client_ip,
        conversation_id=conversation_id,
        team=team,
        topic_label=topic_label,
        language=language,
        question=question,
    )

    from fastapi.responses import StreamingResponse
    import json

    async def event_generator():
        try:
            yield _sse_event("metadata", {
                "status": "metadata",
                "conversation_id": conversation_id,
                "language": language,
                "team": team
            })

            async for event in gateway.ask_stream(
                question,
                team=team,
                conversation_id=conversation_id,
                language=language,
                topic_label=topic_label,
                history=history,
            ):
                event_name = str(event.get("status") or "chunk")
                yield _sse_event(event_name, event)
        except Exception:
            log.exception("Public QA SSE stream failed")
            yield _sse_event(
                "error",
                {"status": "error", "error": _STREAM_ERROR_MESSAGES[language]},
            )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/api/export/wiki")
async def export_wiki(request: Request, background_tasks: BackgroundTasks):
    require_roles(request, {"editor", "admin"})
    if not gateway.online:
        return JSONResponse({"error": "Worker is offline"}, status_code=503)
    
    export_id = f"export-{uuid.uuid4().hex[:12]}"
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    gateway.pending_exports[export_id] = future
    
    try:
        await gateway.send({"type": "create_export", "id": export_id, "export_id": export_id})
        saved_path_str = await asyncio.wait_for(future, timeout=60.0)
        saved_path = Path(saved_path_str)
        
        if not saved_path.is_file():
            return JSONResponse({"error": "Export file not found"}, status_code=500)
            
        def clean_file(p: Path):
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass
                
        background_tasks.add_task(clean_file, saved_path)
        return FileResponse(saved_path, filename="wiki_export.zip", media_type="application/zip")
    except asyncio.TimeoutError:
        return JSONResponse({"error": "Export timed out"}, status_code=504)
    except Exception as exc:
        return JSONResponse({"error": f"Export failed: {str(exc)}"}, status_code=500)
    finally:
        gateway.pending_exports.pop(export_id, None)


@router.post("/api/worker/upload-export/{export_id}")
async def upload_export(
    export_id: str,
    file: UploadFile = File(...),
    x_worker_secret: str = Header(default="", alias="X-Worker-Secret"),
):
    if not WORKER_SHARED_SECRET or x_worker_secret != WORKER_SHARED_SECRET:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
        
    export_dir = DATA_ROOT / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    target_path = export_dir / f"{export_id}.zip"
    
    try:
        def save_file():
            with target_path.open("wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
        await asyncio.to_thread(save_file)
        
        gateway.resolve_export(export_id, str(target_path))
        return {"status": "ok"}
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
