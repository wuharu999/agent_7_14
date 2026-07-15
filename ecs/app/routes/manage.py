from __future__ import annotations

import json

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ecs.app.auth import require_roles, verify_csrf
from ecs.app.database import mark_sources_deleted, write_audit
from ecs.app.gateway import gateway

router = APIRouter()


class DeleteSourceRequest(BaseModel):
    path: str = Field(min_length=1, max_length=1000)


@router.get("/api/manage/sources")
async def list_sources(request: Request):
    session = require_roles(request, {"viewer", "editor", "admin"})
    try:
        result = await gateway.command("list_sources")
    except ConnectionError:
        return JSONResponse({"error": "Worker is offline"}, status_code=503)
    except TimeoutError:
        return JSONResponse({"error": "Worker did not return the source tree in time"}, status_code=504)

    if result.get("status") != "ok":
        return JSONResponse(
            {"error": str(result.get("error") or "Unable to list sources")},
            status_code=500,
        )
    return {
        "worker_online": gateway.online,
        "user": {"username": session["username"], "role": session["role"]},
        "tree": result.get("tree") or {"root": "raw/sources", "children": []},
    }


@router.post("/api/manage/sources/delete")
async def delete_source(
    payload: DeleteSourceRequest,
    request: Request,
    x_csrf_token: str = Header(default="", alias="X-CSRF-Token"),
):
    session = require_roles(request, {"editor", "admin"})
    verify_csrf(session, x_csrf_token)
    source_path = payload.path.strip()

    try:
        result = await gateway.command("delete_source", path=source_path)
    except ConnectionError:
        write_audit(
            user_id=int(session["user_id"]),
            username=str(session["username"]),
            action="delete_source",
            source_path=source_path,
            result="worker_offline",
        )
        return JSONResponse({"error": "Worker is offline"}, status_code=503)
    except TimeoutError:
        write_audit(
            user_id=int(session["user_id"]),
            username=str(session["username"]),
            action="delete_source",
            source_path=source_path,
            result="timeout",
        )
        return JSONResponse({"error": "Worker did not finish the removal in time"}, status_code=504)

    status = str(result.get("status") or "failed")
    if status == "busy":
        write_audit(
            user_id=int(session["user_id"]),
            username=str(session["username"]),
            action="delete_source",
            source_path=source_path,
            result="blocked_processing",
            details=str(result.get("error") or ""),
        )
        return JSONResponse({"error": result.get("error")}, status_code=409)
    if status != "ok":
        write_audit(
            user_id=int(session["user_id"]),
            username=str(session["username"]),
            action="delete_source",
            source_path=source_path,
            result="failed",
            details=str(result.get("error") or ""),
        )
        return JSONResponse(
            {"error": str(result.get("error") or "Unable to remove source")},
            status_code=400,
        )

    deleted_path = str(result.get("path") or source_path)
    mark_sources_deleted(deleted_path)
    write_audit(
        user_id=int(session["user_id"]),
        username=str(session["username"]),
        action="delete_source",
        source_path=deleted_path,
        result="ok",
        details=json.dumps(
            {
                "trash_path": result.get("trash_path"),
                "deleted_files": result.get("deleted_files"),
                "deleted_type": result.get("deleted_type"),
            },
            ensure_ascii=False,
        ),
    )
    return {
        "status": "ok",
        "path": deleted_path,
        "trash_path": result.get("trash_path"),
        "deleted_files": result.get("deleted_files", 0),
        "message": "Source moved to Worker trash. LLM Wiki Source Watch will process the removal.",
    }
