from __future__ import annotations

import asyncio
import re
import shutil
import uuid
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, File, Form, Header, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from ecs.app.auth import require_roles, verify_csrf, check_robot_access
from ecs.app.config import PUBLIC_BASE_URL, UPLOAD_ROOT, WORKER_SHARED_SECRET, TEAM_MAX_UPLOAD_BYTES
from ecs.app.database import get_allowed_teams, create_upload, get_upload, update_upload, write_audit, get_team_upload_usage
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

    allowed_teams_list = get_allowed_teams()
    if team not in allowed_teams_list:
        return JSONResponse(
            {"error": f"Team must be one of {', '.join(allowed_teams_list)}"}, status_code=400
        )
    if not check_robot_access(session, team):
        return JSONResponse(
            {"error": f"You do not have permission to manage the robot '{team}'"}, status_code=403
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

    # Check for unusual activity (more than 5 uploads in 1 minute)
    from ecs.app.database import get_recent_upload_count, _connect, _DB_LOCK
    from ecs.app.mock_email import MockEmailLogger
    recent_uploads = get_recent_upload_count(int(session["user_id"]), minutes=1)
    if recent_uploads >= 5:
        with _DB_LOCK, _connect() as connection:
            admins = connection.execute("SELECT email FROM users WHERE role = 'admin'").fetchall()
        for admin in admins:
            if admin["email"]:
                MockEmailLogger.send_unusual_activity(
                    captain_email=admin["email"],
                    team_name=team,
                    member_username=str(session["username"]),
                    activity_desc=f"User has uploaded more than 5 files in the last minute ({recent_uploads + 1} uploads).",
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

    file_size = target_path.stat().st_size
    current_usage = get_team_upload_usage(team)
    if current_usage + file_size > TEAM_MAX_UPLOAD_BYTES:
        target_path.unlink(missing_ok=True)
        return JSONResponse(
            {"error": f"Upload would exceed team quota of {TEAM_MAX_UPLOAD_BYTES} bytes"},
            status_code=413
        )

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
