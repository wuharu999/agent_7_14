from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import shutil
import time
from pathlib import Path
from typing import Any

import websockets

from worker.claude_runner import GAP_MARKER, run_claude, run_claude_stream
from worker.authoring import AuthoringError, chat as authoring_chat, create_session as create_authoring_session
from worker.authoring import generate_article as generate_authoring_article, get_session as get_authoring_session
from worker.conversation_store import ConversationStore
from worker.config import (
    AUTHORING_LOCK_STRIPES,
    AUTHORING_QUEUE_MAX,
    AUTHORING_WORKERS,
    DOWNLOAD_WORKERS,
    FILE_OPERATION_WORKERS,
    LLM_WIKI_RESCAN_AFTER_PUBLISH,
    QA_WORKERS,
    STAGING_DIR,
    ensure_directories,
    get_team_config,
    websocket_url,
    http_base_url,
    WORKER_ROOT_DIR,
    WORKER_SHARED_SECRET,
)
from worker.downloader import download_file
from worker.file_manager import (
    FileManagerError,
    SourceBusyError,
    list_source_tree,
    soft_delete_source,
)
from worker.knowledge import has_wiki_content, log_unanswered
from worker.llm_wiki_monitor import monitor_source, request_rescan, monitor_global_queue
from worker.models import DownloadJob, FileOperationJob, QuestionJob
from worker.publisher import (
    collect_supported_sources,
    prepare_single_file,
    publish_authoring_article,
    publish_directory,
    safe_segment,
)
from worker.prompt_security import guard_user_input, refusal_text, scan_text_sources
from worker.zip_extractor import extract_zip_safely

log = logging.getLogger("worker.manager")


class WorkerManager:
    def __init__(self) -> None:
        self.question_queues: list[asyncio.Queue[QuestionJob]] = [
            asyncio.Queue(maxsize=100) for _ in range(max(1, QA_WORKERS))
        ]
        self.conversations = ConversationStore()
        self.download_queue: asyncio.Queue[DownloadJob] = asyncio.Queue(maxsize=50)
        self.file_operation_queue: asyncio.Queue[FileOperationJob] = asyncio.Queue(maxsize=50)
        self.authoring_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(
            maxsize=AUTHORING_QUEUE_MAX
        )
        self.authoring_locks = tuple(
            asyncio.Lock() for _ in range(AUTHORING_LOCK_STRIPES)
        )
        self.outgoing: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=500)
        self.websocket = None
        self.connected = asyncio.Event()
        self.active_download_ids: set[str] = set()
        self.active_command_ids: set[str] = set()
        self.active_authoring_ids: set[str] = set()
        self.monitor_tasks: set[asyncio.Task[None]] = set()

    async def run(self) -> None:
        ensure_directories()
        tasks: list[asyncio.Task[Any]] = [
            asyncio.create_task(self.sender_loop(), name="sender"),
            asyncio.create_task(self.connection_loop(), name="connection"),
            asyncio.create_task(monitor_global_queue(self.emit), name="monitor_global_queue"),
        ]
        tasks.extend(
            asyncio.create_task(
                self.qa_worker(index + 1, queue),
                name=f"qa-{index + 1}",
            )
            for index, queue in enumerate(self.question_queues)
        )
        tasks.extend(
            asyncio.create_task(self.download_worker(index + 1), name=f"download-{index + 1}")
            for index in range(DOWNLOAD_WORKERS)
        )
        tasks.extend(
            asyncio.create_task(
                self.file_operation_worker(index + 1),
                name=f"file-operation-{index + 1}",
            )
            for index in range(FILE_OPERATION_WORKERS)
        )
        tasks.extend(
            asyncio.create_task(
                self.authoring_worker(index + 1),
                name=f"authoring-{index + 1}",
            )
            for index in range(AUTHORING_WORKERS)
        )
        await asyncio.gather(*tasks)

    async def emit(self, message: dict[str, Any]) -> None:
        await self.outgoing.put(message)

    async def sender_loop(self) -> None:
        while True:
            message = await self.outgoing.get()
            try:
                while True:
                    await self.connected.wait()
                    websocket = self.websocket
                    if websocket is None:
                        self.connected.clear()
                        continue
                    try:
                        await websocket.send(json.dumps(message, ensure_ascii=False))
                        break
                    except Exception:
                        self.connected.clear()
                        await asyncio.sleep(1)
            finally:
                self.outgoing.task_done()

    async def connection_loop(self) -> None:
        retry = 3.0
        while True:
            try:
                url = websocket_url()
                log.info("Connecting to %s", url.split("?", 1)[0])
                async with websockets.connect(
                    url,
                    ping_interval=20,
                    ping_timeout=10,
                    proxy=None,
                    max_size=16 * 1024 * 1024,
                ) as websocket:
                    self.websocket = websocket
                    self.connected.set()
                    retry = 3.0
                    log.info("Worker connected")
                    asyncio.create_task(self.send_existing_uploads_sync())
                    async for raw in websocket:
                        data = json.loads(raw)
                        await self.route_message(data)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("Worker connection unavailable: %s", exc)
            finally:
                self.websocket = None
                self.connected.clear()
            await asyncio.sleep(retry)
            retry = min(retry * 1.5, 45.0)

    async def send_existing_uploads_sync(self) -> None:
        import os
        from worker.config import ALLOWED_TEAMS, get_team_config
        existing_uploads = []
        for team in ALLOWED_TEAMS:
            tc = get_team_config(team)
            if tc.raw_sources_dir.exists():
                try:
                    for entry in os.scandir(tc.raw_sources_dir):
                        if entry.is_dir():
                            existing_uploads.append({
                                "team": team,
                                "upload_id": entry.name
                            })
                except Exception:
                    log.exception("Failed to scan raw sources directory for team %s", team)
        await self.emit({
            "type": "sync_existing_uploads",
            "uploads": existing_uploads
        })

    async def route_message(self, data: dict[str, Any]) -> None:
        message_type = str(data.get("type") or "")
        if message_type == "question":
            job_id = str(data.get("id") or "")
            conversation_id = str(data.get("conversation_id") or job_id)[:128]
            language = str(data.get("language") or "zh-CN")
            team = str(data.get("team") or "default")
            job = QuestionJob(
                job_id=job_id,
                question=str(data.get("text") or ""),
                team=team,
                conversation_id=conversation_id,
                language=language,
                stream=bool(data.get("stream")),
            )
            lane = self._question_lane(conversation_id)
            await self.question_queues[lane].put(job)
            log.info(
                "Queued %s for conversation %s on QA worker %d",
                job_id,
                conversation_id,
                lane + 1,
            )
            return

        if message_type == "download_file":
            task_id = str(data.get("id") or "")
            if not task_id or task_id in self.active_download_ids:
                return
            self.active_download_ids.add(task_id)
            await self.download_queue.put(
                DownloadJob(
                    task_id=task_id,
                    upload_id=str(data.get("upload_id") or ""),
                    team=str(data.get("team") or "default"),
                    filename=str(data.get("filename") or "uploaded_file"),
                    download_url=str(data.get("download_url") or data.get("url") or ""),
                    published_at_ms=int(data.get("published_at_ms") or 0),
                )
            )
            return

        if message_type in {"list_sources", "delete_source"}:
            command_id = str(data.get("id") or "")
            if not command_id or command_id in self.active_command_ids:
                return
            self.active_command_ids.add(command_id)
            await self.file_operation_queue.put(
                FileOperationJob(
                    command_id=command_id,
                    operation=message_type,
                    payload=data,
                )
            )
            return

        if message_type.startswith("authoring_"):
            command_id = str(data.get("id") or "")
            if not command_id or command_id in self.active_authoring_ids:
                return
            self.active_authoring_ids.add(command_id)
            try:
                self.authoring_queue.put_nowait(data)
            except asyncio.QueueFull:
                self.active_authoring_ids.discard(command_id)
                await self.emit(
                    {
                        "type": "authoring_result",
                        "id": command_id,
                        "status": "failed",
                        "error": "Authoring queue is full; try again shortly",
                    }
                )
                
        if message_type == "trigger_review":
            task_id = str(data.get("id") or "")
            team = str(data.get("team") or "")
            if not task_id or not team:
                return
            asyncio.create_task(self.run_contradiction_review(task_id, team))

        if message_type == "create_export":
            export_id = str(data.get("export_id") or "")
            if export_id:
                asyncio.create_task(self.handle_create_export(export_id))
            return

    @staticmethod
    def _question_lane(conversation_id: str) -> int:
        digest = hashlib.blake2s(
            conversation_id.encode("utf-8", errors="replace"),
            digest_size=4,
        ).digest()
        return int.from_bytes(digest, "big") % max(1, QA_WORKERS)

    async def qa_worker(
        self,
        worker_number: int,
        queue: asyncio.Queue[QuestionJob],
    ) -> None:
        while True:
            job = await queue.get()
            try:
                log.info(
                    "QA worker %d handling %s for conversation %s (%s)",
                    worker_number,
                    job.job_id,
                    job.conversation_id,
                    job.language,
                )
                guard_decision = await guard_user_input(job.question)
                if guard_decision.blocked:
                    await self.emit(
                        {
                            "type": "answer",
                            "id": job.job_id,
                            "conversation_id": job.conversation_id,
                            "text": refusal_text(job.language),
                        }
                    )
                    continue
                has_content = await asyncio.to_thread(has_wiki_content, job.team, job.question)
                if not has_content:
                    await asyncio.to_thread(log_unanswered, job.team, job.question)
                history = self.conversations.history(job.conversation_id)

                if job.stream:
                    accumulated_text = ""
                    sent_length = 0
                    has_gap_marker = False

                    async def on_chunk(text: str, thinking: str, thinking_tokens: int):
                        nonlocal accumulated_text, sent_length, has_gap_marker
                        if thinking or thinking_tokens:
                            await self.emit({
                                "type": "qa_stream_chunk",
                                "id": job.job_id,
                                "conversation_id": job.conversation_id,
                                "text": "",
                                "thinking": thinking,
                                "thinking_tokens": thinking_tokens,
                                "status": "chunk",
                            })
                            return

                        if text:
                            accumulated_text += text
                            if len(accumulated_text) >= len(GAP_MARKER) and accumulated_text.startswith(GAP_MARKER):
                                has_gap_marker = True

                            visible_text = accumulated_text
                            if has_gap_marker:
                                visible_text = accumulated_text[len(GAP_MARKER):]

                            to_send = visible_text[sent_length:]
                            if to_send:
                                sent_length += len(to_send)
                                await self.emit({
                                    "type": "qa_stream_chunk",
                                    "id": job.job_id,
                                    "conversation_id": job.conversation_id,
                                    "text": to_send,
                                    "thinking": "",
                                    "thinking_tokens": 0,
                                    "status": "chunk",
                                })

                    try:
                        answer = await run_claude_stream(
                            job.question,
                            team=job.team,
                            language=job.language,
                            history=history,
                            on_chunk=on_chunk,
                            guard_decision=guard_decision,
                        )
                        if has_gap_marker or answer.startswith(GAP_MARKER):
                            await asyncio.to_thread(log_unanswered, job.team, job.question)
                            if answer.startswith(GAP_MARKER):
                                answer = answer[len(GAP_MARKER):].strip()

                        self.conversations.append(job.conversation_id, job.question, answer)
                        await self.emit({
                            "type": "qa_stream_chunk",
                            "id": job.job_id,
                            "conversation_id": job.conversation_id,
                            "status": "done",
                        })
                    except Exception as stream_exc:
                        log.exception("Claude streaming failed")
                        await self.emit({
                            "type": "qa_stream_chunk",
                            "id": job.job_id,
                            "conversation_id": job.conversation_id,
                            "status": "error",
                            "error": str(stream_exc),
                        })
                else:
                    answer = await run_claude(
                        job.question,
                        team=job.team,
                        language=job.language,
                        history=history,
                        guard_decision=guard_decision,
                    )
                    if answer.startswith(GAP_MARKER):
                        await asyncio.to_thread(log_unanswered, job.team, job.question)
                        answer = answer[len(GAP_MARKER):].strip()
                    self.conversations.append(job.conversation_id, job.question, answer)
                    await self.emit(
                        {
                            "type": "answer",
                            "id": job.job_id,
                            "conversation_id": job.conversation_id,
                            "text": answer,
                        }
                    )
            except Exception as exc:
                log.exception("QA job failed")
                if job.stream:
                    await self.emit({
                        "type": "qa_stream_chunk",
                        "id": job.job_id,
                        "conversation_id": job.conversation_id,
                        "status": "error",
                        "error": str(exc),
                    })
                else:
                    await self.emit(
                        {
                            "type": "answer",
                            "id": job.job_id,
                            "conversation_id": job.conversation_id,
                            "text": f"[错误] Worker QA failed: {exc}",
                        }
                    )
            finally:
                queue.task_done()

    def _authoring_lock(self, data: dict[str, Any]) -> asyncio.Lock:
        identity = str(
            data.get("session_id") or data.get("article_id") or data.get("id") or ""
        )
        digest = hashlib.blake2s(
            identity.encode("utf-8", errors="replace"),
            digest_size=4,
        ).digest()
        return self.authoring_locks[
            int.from_bytes(digest, "big") % len(self.authoring_locks)
        ]

    async def authoring_worker(self, worker_number: int) -> None:
        while True:
            data = await self.authoring_queue.get()
            command_id = str(data.get("id") or "")
            try:
                async with self._authoring_lock(data):
                    log.info(
                        "Authoring worker %d handling %s (%s)",
                        worker_number,
                        command_id,
                        data.get("type"),
                    )
                    await self.authoring_command(data)
            finally:
                self.active_authoring_ids.discard(command_id)
                self.authoring_queue.task_done()

    async def authoring_command(self, data: dict[str, Any]) -> None:
        command_id = str(data.get("id") or "")
        message_type = str(data.get("type") or "")
        try:
            session_id = str(data.get("session_id") or "")
            if message_type == "authoring_create":
                result = await asyncio.to_thread(
                    create_authoring_session, session_id, str(data.get("team") or "")
                )
            elif message_type == "authoring_history":
                result = await asyncio.to_thread(get_authoring_session, session_id)
            elif message_type == "authoring_chat":
                _session, answer = await authoring_chat(
                    session_id, str(data.get("message") or "")
                )
                result = {"answer": answer}
            elif message_type == "authoring_generate":
                result = {"markdown": await generate_authoring_article(session_id)}
            elif message_type == "authoring_publish":
                article_id = str(data.get("article_id") or "")
                published_at_ms = int(time.time() * 1000)
                source_path = await asyncio.to_thread(
                    publish_authoring_article,
                    article_id,
                    str(data.get("team") or ""),
                    str(data.get("title") or "article"),
                    str(data.get("markdown") or ""),
                )
                result = {"source_path": source_path, "article_id": article_id}

                async def emit_authoring(event: dict[str, Any]) -> None:
                    await self.emit({
                        "type": "authoring_progress",
                        "article_id": article_id,
                        "source_identity": event.get("source_identity"),
                        "source_status": event.get("source_status"),
                        "error": event.get("error"),
                    })

                task = asyncio.create_task(
                    monitor_source(
                        upload_id=article_id,
                        source_identity=source_path,
                        published_at_ms=published_at_ms,
                        emit=emit_authoring,
                    ),
                    name=f"llm-wiki-authoring:{article_id}",
                )
                self.monitor_tasks.add(task)
                task.add_done_callback(self.monitor_tasks.discard)
            else:
                return
            await self.emit({"type": "authoring_result", "id": command_id, "status": "ok", **(result or {})})
        except (AuthoringError, FileExistsError, ValueError) as exc:
            await self.emit({"type": "authoring_result", "id": command_id, "status": "failed", "error": str(exc)})
        except Exception as exc:
            log.exception("Authoring command failed")
            await self.emit({"type": "authoring_result", "id": command_id, "status": "failed", "error": "Worker authoring operation failed"})

    async def file_operation_worker(self, worker_number: int) -> None:
        while True:
            job = await self.file_operation_queue.get()
            try:
                log.info(
                    "File-operation worker %d handling %s (%s)",
                    worker_number,
                    job.command_id,
                    job.operation,
                )
                if job.operation == "list_sources":
                    tree = await asyncio.to_thread(list_source_tree)
                    await self.emit(
                        {
                            "type": "source_tree_result",
                            "id": job.command_id,
                            "status": "ok",
                            "tree": tree,
                        }
                    )
                elif job.operation == "delete_source":
                    relative_path = str(job.payload.get("path") or "")
                    result = await asyncio.to_thread(soft_delete_source, relative_path)
                    await self.emit(
                        {
                            "type": "delete_source_result",
                            "id": job.command_id,
                            "status": "ok",
                            **result,
                        }
                    )
            except SourceBusyError as exc:
                await self.emit(
                    {
                        "type": "delete_source_result",
                        "id": job.command_id,
                        "status": "busy",
                        "error": str(exc),
                    }
                )
            except FileManagerError as exc:
                result_type = (
                    "source_tree_result"
                    if job.operation == "list_sources"
                    else "delete_source_result"
                )
                await self.emit(
                    {
                        "type": result_type,
                        "id": job.command_id,
                        "status": "failed",
                        "error": str(exc),
                    }
                )
            except Exception as exc:
                log.exception("File operation failed")
                result_type = (
                    "source_tree_result"
                    if job.operation == "list_sources"
                    else "delete_source_result"
                )
                await self.emit(
                    {
                        "type": result_type,
                        "id": job.command_id,
                        "status": "failed",
                        "error": f"Worker file operation failed: {exc}",
                    }
                )
            finally:
                self.active_command_ids.discard(job.command_id)
                self.file_operation_queue.task_done()

    async def download_worker(self, worker_number: int) -> None:
        while True:
            job = await self.download_queue.get()
            try:
                log.info("Download worker %d handling %s", worker_number, job.upload_id)
                await self.process_download(job)
                await self.emit(
                    {
                        "type": "download_result",
                        "id": job.task_id,
                        "upload_id": job.upload_id,
                        "status": "ok",
                    }
                )
            except Exception as exc:
                log.exception("Download/publish failed for %s", job.upload_id)
                await self.emit(
                    {
                        "type": "job_progress",
                        "upload_id": job.upload_id,
                        "status": "download_failed",
                        "stage": "download_failed",
                        "message": "Worker failed while downloading or publishing",
                        "error": str(exc),
                    }
                )
                await self.emit(
                    {
                        "type": "download_result",
                        "id": job.task_id,
                        "upload_id": job.upload_id,
                        "status": "failed",
                        "error": str(exc),
                    }
                )
            finally:
                self.active_download_ids.discard(job.task_id)
                self.download_queue.task_done()

    async def process_download(self, job: DownloadJob) -> None:
        if not job.upload_id or not job.download_url:
            raise ValueError("download task is missing upload_id or download_url")

        tc = get_team_config(job.team)
        safe_team = safe_segment(job.team, "default")
        safe_upload_id = safe_segment(job.upload_id, "upload")
        final_directory = tc.raw_sources_dir / safe_upload_id
        published_at_ms = job.published_at_ms
        if final_directory.exists():
            existing_sources = collect_supported_sources(final_directory)
            identities = [
                f"{job.team}/{path.relative_to(tc.raw_sources_dir).as_posix()}"
                for path in existing_sources
            ]
            if identities:
                await self.scan_and_report_sources(
                    job.upload_id,
                    existing_sources,
                    final_directory,
                    f"{safe_team}/{safe_upload_id}",
                )
                await self.publish_and_monitor(job.upload_id, identities, published_at_ms)
                return

        job_root = STAGING_DIR / job.upload_id
        if job_root.exists():
            await asyncio.to_thread(shutil.rmtree, job_root)
        original_dir = job_root / "original"
        publish_dir = job_root / "publish"
        original_dir.mkdir(parents=True, exist_ok=True)
        downloaded_path = original_dir / job.filename

        async def on_download_progress(percent: int | None, message: str) -> None:
            await self.emit(
                {
                    "type": "job_progress",
                    "upload_id": job.upload_id,
                    "status": "downloading",
                    "stage": "downloading",
                    "message": message,
                    "percent": percent,
                }
            )

        await self.emit(
            {
                "type": "job_progress",
                "upload_id": job.upload_id,
                "status": "downloading",
                "stage": "downloading",
                "message": "Worker started downloading from ECS",
                "percent": 0,
            }
        )
        await download_file(
            url=job.download_url,
            destination=downloaded_path,
            on_progress=on_download_progress,
        )

        if downloaded_path.suffix.lower() == ".zip":
            await self.emit(
                {
                    "type": "job_progress",
                    "upload_id": job.upload_id,
                    "status": "extracting",
                    "stage": "extracting",
                    "message": "Safely extracting ZIP file",
                    "percent": None,
                }
            )
            await asyncio.to_thread(extract_zip_safely, downloaded_path, publish_dir)
        else:
            await asyncio.to_thread(prepare_single_file, downloaded_path, publish_dir)

        supported = await asyncio.to_thread(collect_supported_sources, publish_dir)
        if not supported:
            raise ValueError("No LLM Wiki-supported source files found after preparation")

        await self.scan_and_report_sources(
            job.upload_id,
            supported,
            publish_dir,
            f"{safe_team}/{safe_upload_id}",
        )

        await self.emit(
            {
                "type": "job_progress",
                "upload_id": job.upload_id,
                "status": "publishing_sources",
                "stage": "publishing_sources",
                "message": "Publishing complete source bundle into raw/sources",
                "percent": 100,
            }
        )
        _, identities = await asyncio.to_thread(
            publish_directory,
            publish_dir,
            job.team,
            job.upload_id,
        )
        published_at_ms = int(time.time() * 1000)
        await self.publish_and_monitor(job.upload_id, identities, published_at_ms)
        await asyncio.to_thread(shutil.rmtree, job_root, True)

    async def scan_and_report_sources(
        self,
        upload_id: str,
        paths: list[Path],
        root: Path,
        identity_prefix: str,
    ) -> None:
        scan = await asyncio.to_thread(scan_text_sources, paths, root)
        warnings = [
            {
                "source_identity": f"{identity_prefix}/{warning['source_identity']}",
                "categories": warning["categories"],
            }
            for warning in scan.warnings
        ]
        await self.emit(
            {
                "type": "upload_security_warnings",
                "upload_id": upload_id,
                "warnings": warnings,
                "security_scan_complete": scan.complete,
            }
        )

    async def publish_and_monitor(
        self,
        upload_id: str,
        identities: list[str],
        published_at_ms: int,
    ) -> None:
        await self.emit(
            {
                "type": "sources_published",
                "upload_id": upload_id,
                "source_identities": identities,
                "published_at_ms": published_at_ms,
            }
        )
        # Source Watch + Auto Ingest is the normal trigger. Rescan is disabled by
        # default because invoking both can make the same source appear twice.
        if LLM_WIKI_RESCAN_AFTER_PUBLISH:
            await request_rescan()
        for identity in identities:
            task = asyncio.create_task(
                monitor_source(
                    upload_id=upload_id,
                    source_identity=identity,
                    published_at_ms=published_at_ms,
                    emit=self.emit,
                ),
                name=f"llm-wiki-monitor:{upload_id}:{identity}",
            )
            self.monitor_tasks.add(task)
            task.add_done_callback(self.monitor_tasks.discard)

    async def run_contradiction_review(self, task_id: str, team: str) -> None:
        log.info("Running contradiction review for team %s", team)
        team_config = get_team_config(team)
        wiki_dir = team_config.project_dir / "wiki"
        if not wiki_dir.is_dir():
            log.info("No wiki directory found for team %s", team)
            return

        files = list(wiki_dir.rglob("*.md"))
        if not files:
            log.info("No wiki files found for team %s", team)
            return

        # Simple prompt for Claude
        prompt = (
            f"Please read the following wiki files for the team '{team}' and identify any "
            f"contradictions or conflicting information between them. If you find contradictions, "
            f"list them clearly. If you don't find any, just reply 'No contradictions found.'\n\n"
        )
        # We might have too many files, so we'll just read the first 10 for simplicity in this prototype.
        for f in files[:10]:
            try:
                content = f.read_text(encoding="utf-8")
                prompt += f"--- File: {f.name} ---\n{content}\n\n"
            except Exception:
                pass

        try:
            result = await run_claude(
                prompt=prompt,
                allowed_tools=["Read"],
                timeout=180,
                max_tokens=4000
            )
            answer = result["answer"]
            
            if "No contradictions found" not in answer:
                await self.emit({
                    "type": "contradiction_alert",
                    "team": team,
                    "details": answer
                })
        except Exception as e:
            log.error(f"Contradiction review failed: {e}")

    async def handle_create_export(self, export_id: str) -> None:
        import httpx
        import tempfile
        import shutil

        log.info("Starting wiki export for export_id %s", export_id)
        wiki_dir = WORKER_ROOT_DIR / "wiki"

        if not wiki_dir.is_dir():
            log.error("Wiki directory %s does not exist", wiki_dir)
            return

        tmp_dir = tempfile.gettempdir()
        archive_base = Path(tmp_dir) / f"wiki_export_{export_id}"
        zip_path = Path(tmp_dir) / f"wiki_export_{export_id}.zip"

        try:
            def create_zip():
                shutil.make_archive(str(archive_base), 'zip', root_dir=str(wiki_dir))
            await asyncio.to_thread(create_zip)

            if not zip_path.is_file():
                raise FileNotFoundError(f"Failed to create archive at {zip_path}")

            http_base = http_base_url()
            upload_url = f"{http_base}/api/worker/upload-export/{export_id}"
            headers = {
                "X-Worker-Secret": WORKER_SHARED_SECRET,
                "User-Agent": "agent-7-14-worker/1.0",
            }

            async with httpx.AsyncClient(timeout=120.0, trust_env=False) as client:
                with zip_path.open("rb") as f:
                    files = {"file": (f"wiki_export_{export_id}.zip", f, "application/zip")}
                    response = await client.post(upload_url, headers=headers, files=files)
                    response.raise_for_status()
            log.info("Successfully uploaded wiki export %s", export_id)
        except Exception as e:
            log.exception("Failed to handle create_export for %s", export_id)
        finally:
            try:
                zip_path.unlink(missing_ok=True)
            except Exception:
                pass
