from __future__ import annotations

import asyncio
import re
import shutil
import uuid
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, File, Form, Header, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from ecs.app.auth import require_roles, verify_csrf
from ecs.app.config import ALLOWED_TEAMS, PUBLIC_BASE_URL, UPLOAD_ROOT, WORKER_SHARED_SECRET
from ecs.app.database import create_upload, get_upload, update_upload, write_audit
from ecs.app.gateway import gateway
from shared.source_types import SUPPORTED_UPLOAD_SUFFIXES, is_supported_upload

router = APIRouter()


def safe_filename(name: str) -> str:
    value = (name or "uploaded_file").strip().replace("/", "_").replace("\\", "_")
    value = re.sub(r"[^a-zA-Z0-9._\-\u4e00-\u9fff]+", "_", value)
    if not value:
        return "uploaded_file"
    suffix = Path(value).suffix
    if not suffix or len(value) <= 180:
        return value[:180]
    stem = value[: -len(suffix)] or "uploaded_file"
    return f"{stem[: 180 - len(suffix)]}{suffix}"


def safe_segment(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._\-]+", "_", (value or "").strip())
    return cleaned[:100] or "invalid"


def _copy_upload_sync(source, destination: Path) -> None:
    with destination.open("wb") as output:
        shutil.copyfileobj(source, output)


async def dispatch_upload(upload: dict, request: Request | None = None) -> None:
    base_url = PUBLIC_BASE_URL
    if not base_url and request is not None:
        base_url = str(request.base_url).rstrip("/")
    if not base_url:
        update_upload(
            upload["upload_id"],
            status="waiting_for_worker",
            stage="configuration_error",
            message="PUBLIC_BASE_URL is not configured",
            error="PUBLIC_BASE_URL is required for Worker downloads",
        )
        return

    download_url = f"{base_url}/download/{quote(upload['upload_id'])}/{quote(upload['filename'])}"
    message = {
        "type": "download_file",
        "id": upload["task_id"],
        "upload_id": upload["upload_id"],
        "team": upload["team"],
        "filename": upload["filename"],
        "download_url": download_url,
        "published_at_ms": int(upload.get("published_at_ms") or 0),
    }
    try:
        await gateway.send(message)
        update_upload(
            upload["upload_id"],
            status="queued_for_worker",
            stage="queued_for_worker",
            message="Upload saved on ECS and queued for the Worker",
            percent=0,
            error=None,
        )
    except ConnectionError:
        update_upload(
            upload["upload_id"],
            status="waiting_for_worker",
            stage="waiting_for_worker",
            message="Upload saved on ECS; waiting for Worker connection",
            percent=0,
            error=None,
        )


@router.post("/upload", status_code=202)
async def upload_file(
    request: Request,
    team: str = Form(...),
    file: UploadFile = File(...),
    csrf_token: str = Form(...),
):
    session = require_roles(request, {"editor", "admin"})
    verify_csrf(session, csrf_token)

    if team not in ALLOWED_TEAMS:
        return JSONResponse(
            {"error": "invalid team", "allowed_teams": list(ALLOWED_TEAMS)},
            status_code=400,
        )

    original_filename = file.filename or "uploaded_file"
    if not is_supported_upload(original_filename):
        await file.close()
        return JSONResponse(
            {
                "error": "unsupported file type",
                "allowed_extensions": sorted(SUPPORTED_UPLOAD_SUFFIXES),
            },
            status_code=400,
        )

    upload_id = uuid.uuid4().hex[:16]
    task_id = f"dl-{uuid.uuid4().hex[:16]}"
    filename = safe_filename(original_filename)
    target_dir = UPLOAD_ROOT / upload_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / filename

    try:
        await asyncio.to_thread(_copy_upload_sync, file.file, target_path)
    finally:
        await file.close()

    create_upload(
        upload_id=upload_id,
        task_id=task_id,
        team=team,
        filename=filename,
        size_bytes=target_path.stat().st_size,
        ecs_path=str(target_path),
        status="waiting_for_worker",
        stage="uploaded_to_ecs",
        message="Upload saved on ECS",
        created_by=int(session["user_id"]),
    )
    write_audit(
        user_id=int(session["user_id"]),
        username=str(session["username"]),
        action="upload_source",
        source_path=f"{team}/{upload_id}/{filename}",
        result="accepted",
        details=f"size_bytes={target_path.stat().st_size}",
    )
    upload = get_upload(upload_id)
    assert upload is not None
    await dispatch_upload(upload, request)

    return {
        "upload_id": upload_id,
        "status": "queued" if gateway.online else "waiting_for_worker",
        "status_url": f"/api/uploads/{upload_id}",
        "status_page": f"/uploads/{upload_id}",
    }


@router.get("/download/{upload_id}/{filename}")
async def download_file(
    upload_id: str,
    filename: str,
    x_worker_secret: str = Header(default="", alias="X-Worker-Secret"),
):
    if not WORKER_SHARED_SECRET or x_worker_secret != WORKER_SHARED_SECRET:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    safe_id = safe_segment(upload_id)
    safe_name = safe_filename(filename)
    upload = get_upload(safe_id)
    if upload is None or upload["filename"] != safe_name:
        return JSONResponse({"error": "file not found"}, status_code=404)
    path = Path(upload["ecs_path"])
    if not path.is_file():
        return JSONResponse({"error": "file not found"}, status_code=404)
    return FileResponse(path, filename=safe_name)
