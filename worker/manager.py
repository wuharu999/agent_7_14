from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import shutil
import time
from typing import Any

import websockets

from worker.claude_runner import GAP_MARKER, run_claude
from worker.conversation_store import ConversationStore
from worker.config import (
    DOWNLOAD_WORKERS,
    FILE_OPERATION_WORKERS,
    LLM_WIKI_RESCAN_AFTER_PUBLISH,
    QA_WORKERS,
    RAW_SOURCES_DIR,
    STAGING_DIR,
    ensure_directories,
    websocket_url,
)
from worker.downloader import download_file
from worker.file_manager import (
    FileManagerError,
    SourceBusyError,
    list_source_tree,
    soft_delete_source,
)
from worker.knowledge import has_wiki_content, log_unanswered
from worker.llm_wiki_monitor import monitor_source, request_rescan
from worker.models import DownloadJob, FileOperationJob, QuestionJob
from worker.publisher import collect_supported_sources, prepare_single_file, publish_directory
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
        self.outgoing: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=500)
        self.websocket = None
        self.connected = asyncio.Event()
        self.active_download_ids: set[str] = set()
        self.active_command_ids: set[str] = set()
        self.monitor_tasks: set[asyncio.Task[None]] = set()

    async def run(self) -> None:
        ensure_directories()
        tasks: list[asyncio.Task[Any]] = [
            asyncio.create_task(self.sender_loop(), name="sender"),
            asyncio.create_task(self.connection_loop(), name="connection"),
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

    async def route_message(self, data: dict[str, Any]) -> None:
        message_type = str(data.get("type") or "")
        if message_type == "question":
            job_id = str(data.get("id") or "")
            conversation_id = str(data.get("conversation_id") or job_id)[:128]
            language = str(data.get("language") or "zh-CN")
            job = QuestionJob(
                job_id=job_id,
                question=str(data.get("text") or ""),
                conversation_id=conversation_id,
                language=language,
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
                has_content = await asyncio.to_thread(has_wiki_content, job.question)
                if not has_content:
                    await asyncio.to_thread(log_unanswered, job.question)
                history = self.conversations.history(job.conversation_id)
                answer = await run_claude(
                    job.question,
                    language=job.language,
                    history=history,
                )
                if answer.startswith(GAP_MARKER):
                    await asyncio.to_thread(log_unanswered, job.question)
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

        final_directory = RAW_SOURCES_DIR / job.team / job.upload_id
        published_at_ms = job.published_at_ms
        if final_directory.exists():
            identities = [
                path.relative_to(RAW_SOURCES_DIR).as_posix()
                for path in collect_supported_sources(final_directory)
            ]
            if identities:
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
