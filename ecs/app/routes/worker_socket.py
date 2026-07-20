from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from ecs.app.config import WORKER_SHARED_SECRET
from ecs.app.database import (
    list_dispatchable_uploads,
    replace_upload_security_warnings,
    register_sources,
    update_upload,
    upsert_source,
    update_authoring_article,
    reconcile_existing_uploads,
)
from ecs.app.gateway import gateway
from ecs.app.routes.uploads import dispatch_upload
from ecs.app.security_warnings import validated_security_warnings

router = APIRouter()
log = logging.getLogger("ecs.worker_socket")


async def _dispatch_waiting_uploads() -> None:
    for upload in list_dispatchable_uploads():
        await dispatch_upload(upload)


@router.websocket("/ws/client")
async def worker_socket(ws: WebSocket, secret: str = Query(default="")):
    if not WORKER_SHARED_SECRET or secret != WORKER_SHARED_SECRET:
        await ws.close(code=1008)
        return

    await ws.accept()
    await gateway.attach(ws)
    await _dispatch_waiting_uploads()
    try:
        while True:
            data = await ws.receive_json()
            message_type = data.get("type")

            if message_type == "answer":
                gateway.resolve_answer(str(data.get("id") or ""), str(data.get("text") or ""))

            elif message_type == "qa_stream_chunk":
                gateway.resolve_stream_chunk(str(data.get("id") or ""), data)

            elif message_type in {"source_tree_result", "delete_source_result"}:
                gateway.resolve_command(str(data.get("id") or ""), data)

            elif message_type == "authoring_result":
                gateway.resolve_command(str(data.get("id") or ""), data)

            elif message_type == "authoring_progress":
                article_id = str(data.get("article_id") or data.get("upload_id") or "")
                if article_id:
                    status = str(data.get("source_status") or "waiting")
                    update_authoring_article(
                        article_id,
                        status=status,
                        error=data.get("error"),
                    )

            elif message_type == "contradiction_alert":
                team = str(data.get("team") or "")
                details = str(data.get("details") or "")
                if team and details:
                    from ecs.app.database import get_team_captains
                    from ecs.app.mock_email import MockEmailLogger
                    captains = get_team_captains(team)
                    for captain in captains:
                        if captain.get("email"):
                            MockEmailLogger.send_contradiction_alert(
                                to_email=captain["email"],
                                team_name=team,
                                contradiction_details=details
                            )

            elif message_type == "llm_wiki_snapshot":
                gateway.latest_snapshot = data

            elif message_type == "sync_existing_uploads":
                uploads_on_disk = data.get("uploads") or []
                await asyncio.to_thread(reconcile_existing_uploads, uploads_on_disk)

            elif message_type == "upload_security_warnings":
                upload_id = str(data.get("upload_id") or "")
                if upload_id:
                    await asyncio.to_thread(
                        replace_upload_security_warnings,
                        upload_id,
                        validated_security_warnings(data.get("warnings")),
                        complete=data.get("security_scan_complete") is True,
                    )

            elif message_type == "job_progress":
                upload_id = str(data.get("upload_id") or "")
                if not upload_id:
                    continue
                source_identity = str(data.get("source_identity") or "")
                if source_identity:
                    upsert_source(
                        upload_id=upload_id,
                        source_identity=source_identity,
                        status=str(data.get("source_status") or "waiting"),
                        error=data.get("error"),
                        files_written=list(data.get("files_written") or []),
                        retry_count=int(data.get("retry_count") or 0),
                        max_retries=int(data.get("max_retries") or 0),
                        active_queue_count=int(data.get("active_queue_count") or 0),
                    )
                else:
                    update_upload(
                        upload_id,
                        status=str(data.get("status") or data.get("stage") or "running"),
                        stage=str(data.get("stage") or "running"),
                        message=str(data.get("message") or ""),
                        percent=data.get("percent"),
                        error=data.get("error"),
                        published_at_ms=data.get("published_at_ms"),
                    )

            elif message_type == "sources_published":
                upload_id = str(data.get("upload_id") or "")
                sources = [str(item) for item in data.get("source_identities") or []]
                published_at_ms = int(data.get("published_at_ms") or 0)
                if upload_id and sources:
                    register_sources(upload_id, sources, published_at_ms)

            elif message_type == "download_result":
                upload_id = str(data.get("upload_id") or "")
                status = str(data.get("status") or "failed")
                if upload_id and status != "ok":
                    update_upload(
                        upload_id,
                        status="download_failed",
                        stage="download_failed",
                        message="Worker failed to process the upload",
                        error=str(data.get("error") or "unknown Worker error"),
                    )
    except WebSocketDisconnect:
        log.warning("Worker disconnected")
    except Exception:
        log.exception("Worker WebSocket error")
    finally:
        await gateway.detach(ws)
