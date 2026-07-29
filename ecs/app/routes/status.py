from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from ecs.app.auth import require_user
from ecs.app.database import (
    get_recent_llm_wiki_source_counts,
    get_upload,
    list_recent_uploads_with_sources,
    list_uploads,
)
from ecs.app.gateway import gateway

router = APIRouter()


@router.get("/health")
async def health():
    return {
        "status": "ok",
        "worker_online": gateway.online,
        "pending_questions": len(gateway.pending_answers),
        "pending_commands": len(gateway.pending_commands),
    }


@router.get("/api/uploads")
async def uploads_api(request: Request, limit: int = Query(50, ge=1, le=200)):
    require_user(request)
    return {"uploads": list_uploads(limit)}


@router.get("/api/uploads/recent")
async def recent_uploads_api(
    request: Request,
    hours: int = Query(24, ge=1, le=168),
    limit: int = Query(200, ge=1, le=200),
):
    require_user(request)
    return {"uploads": list_recent_uploads_with_sources(hours=hours, limit=limit)}


@router.get("/api/uploads/{upload_id}")
async def upload_status_api(upload_id: str, request: Request):
    require_user(request)
    upload = get_upload(upload_id)
    if upload is None:
        return JSONResponse({"error": "upload not found"}, status_code=404)
    upload["worker_online"] = gateway.online
    return upload


@router.get("/api/status/llm_wiki")
async def llm_wiki_status_api(request: Request):
    require_user(request)
    return {
        "counts": get_recent_llm_wiki_source_counts(hours=24),
        "hours": 24,
        "worker_online": gateway.online,
    }
