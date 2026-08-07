from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Awaitable, Callable

from fastapi import WebSocket

from ecs.app.config import FILE_COMMAND_TIMEOUT, WORKER_TIMEOUT

log = logging.getLogger("ecs.gateway")


class WorkerGateway:
    def __init__(self) -> None:
        self.websocket: WebSocket | None = None
        self.outgoing: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=500)
        self.pending_answers: dict[str, asyncio.Future[str]] = {}
        self.pending_commands: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self.pending_command_progress: dict[
            str, Callable[[dict[str, Any]], Awaitable[None]]
        ] = {}
        self.pending_streams: dict[str, asyncio.Queue[dict[str, Any]]] = {}
        self.pending_exports: dict[str, asyncio.Future[str]] = {}
        self.sender_task: asyncio.Task[None] | None = None
        self._connection_lock = asyncio.Lock()
        self.latest_snapshot: dict[str, Any] = {}

    @property
    def online(self) -> bool:
        return self.websocket is not None

    async def attach(self, websocket: WebSocket) -> None:
        async with self._connection_lock:
            if self.websocket is not None and self.websocket is not websocket:
                try:
                    await self.websocket.close(code=1012)
                except Exception:
                    pass
            self.websocket = websocket
            if self.sender_task and not self.sender_task.done():
                self.sender_task.cancel()
            self.sender_task = asyncio.create_task(self._sender_loop(websocket))
        log.info("Worker WebSocket attached")

    async def detach(self, websocket: WebSocket) -> None:
        async with self._connection_lock:
            if self.websocket is not websocket:
                return
            self.websocket = None
            if self.sender_task and not self.sender_task.done():
                self.sender_task.cancel()
            self.sender_task = None
        log.warning("Worker WebSocket detached (granting 120s reconnection grace period)")
        asyncio.create_task(self._graceful_disconnect_cleanup(websocket))

    async def _graceful_disconnect_cleanup(self, detached_ws: WebSocket) -> None:
        await asyncio.sleep(120.0)
        async with self._connection_lock:
            if self.websocket is not None:
                log.info("Worker reconnected within 120s grace period; active background jobs preserved")
                return
        error = ConnectionError("Worker disconnected")
        for qid, future in list(self.pending_answers.items()):
            if not future.done():
                future.set_exception(error)
            self.pending_answers.pop(qid, None)
        for command_id, future in list(self.pending_commands.items()):
            if not future.done():
                future.set_exception(error)
            self.pending_commands.pop(command_id, None)
        for export_id, future in list(self.pending_exports.items()):
            if not future.done():
                future.set_exception(error)
            self.pending_exports.pop(export_id, None)
        for qid, queue in list(self.pending_streams.items()):
            queue.put_nowait({"status": "error", "error": "Worker disconnected"})
            self.pending_streams.pop(qid, None)
        log.warning("Reconnection grace period expired; pending worker jobs marked failed")

    async def _sender_loop(self, websocket: WebSocket) -> None:
        while True:
            message = await self.outgoing.get()
            sent = False
            try:
                await websocket.send_json(message)
                sent = True
            finally:
                self.outgoing.task_done()
                if not sent:
                    await self.outgoing.put(message)

    async def send(self, message: dict[str, Any]) -> None:
        if not self.online:
            raise ConnectionError("Worker is not connected")
        await self.outgoing.put(message)

    async def ask_stream(
        self,
        question: str,
        *,
        team: str = "all",
        conversation_id: str,
        language: str,
        timeout: int | None = None,
    ) -> Any:
        if not self.online:
            raise ConnectionError("Worker is not connected")
        qid = f"q-{uuid.uuid4().hex[:12]}"
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.pending_streams[qid] = queue
        try:
            await self.send(
                {
                    "type": "question",
                    "id": qid,
                    "team": team,
                    "text": question,
                    "conversation_id": conversation_id,
                    "language": language,
                    "stream": True,
                }
            )
            import time
            start_time = time.time()
            max_duration = timeout or WORKER_TIMEOUT
            while True:
                elapsed = time.time() - start_time
                if elapsed >= max_duration:
                    raise TimeoutError("Claude streaming timed out")
                
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=max_duration - elapsed)
                except asyncio.TimeoutError:
                    raise TimeoutError("Claude streaming timed out")

                yield event
                if event.get("status") in ("done", "error"):
                    break
        finally:
            self.pending_streams.pop(qid, None)

    async def ask(
        self,
        question: str,
        *,
        team: str = "all",
        conversation_id: str,
        language: str,
        timeout: int | None = None,
    ) -> str:
        full_text = []
        async for event in self.ask_stream(
            question,
            team=team,
            conversation_id=conversation_id,
            language=language,
            timeout=timeout,
        ):
            if event.get("status") == "error":
                raise RuntimeError(event.get("error") or "Unknown streaming error")
            if isinstance(event.get("replace_text"), str):
                full_text = [str(event["replace_text"])]
            if event.get("text"):
                full_text.append(str(event["text"]))
        return "".join(full_text)

    def resolve_stream_chunk(self, qid: str, chunk: dict[str, Any]) -> None:
        queue = self.pending_streams.get(qid)
        if queue is not None:
            queue.put_nowait(chunk)

    async def command(
        self,
        message_type: str,
        *,
        timeout: int | None = None,
        on_progress: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        **payload: Any,
    ) -> dict[str, Any]:
        if not self.online:
            raise ConnectionError("Worker is not connected")
        command_id = f"cmd-{uuid.uuid4().hex[:16]}"
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self.pending_commands[command_id] = future
        if on_progress is not None:
            self.pending_command_progress[command_id] = on_progress
        try:
            await self.send({"type": message_type, "id": command_id, **payload})
            return await asyncio.wait_for(
                future,
                timeout=timeout or FILE_COMMAND_TIMEOUT,
            )
        finally:
            self.pending_commands.pop(command_id, None)
            self.pending_command_progress.pop(command_id, None)

    async def send_command(
        self,
        message_type: str,
        *args: Any,
        timeout: int | float | None = None,
        **payload: Any,
    ) -> dict[str, Any]:
        return await self.command(message_type, timeout=int(timeout) if timeout is not None else None, **payload)

    def resolve_answer(self, qid: str, answer: str) -> None:
        future = self.pending_answers.get(qid)
        if future is not None and not future.done():
            future.set_result(answer)

    def resolve_command(self, command_id: str, result: dict[str, Any]) -> None:
        future = self.pending_commands.get(command_id)
        if future is not None and not future.done():
            future.set_result(result)

    async def resolve_command_progress(
        self, command_id: str, event: dict[str, Any]
    ) -> None:
        callback = self.pending_command_progress.get(command_id)
        if callback is not None:
            await callback(event)

    def resolve_export(self, export_id: str, path: str) -> None:
        future = self.pending_exports.get(export_id)
        if future is not None and not future.done():
            future.set_result(path)


gateway = WorkerGateway()
