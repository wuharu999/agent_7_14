from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import worker.authoring as authoring
import worker.publisher as publisher
from worker.manager import WorkerManager


class FakeClaudeProcess:
    def __init__(self) -> None:
        self.returncode = 0
        self.input: bytes | None = None

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
        self.input = input
        return b"Claude response", b""


class AuthoringPromptTests(unittest.IsolatedAsyncioTestCase):
    def test_context_is_bounded_from_the_oldest_turns(self) -> None:
        messages = [
            {"role": "user", "content": "old-" + "x" * 100},
            {"role": "assistant", "content": "new answer"},
        ]
        with patch.object(authoring, "AUTHORING_MAX_CONTEXT_BYTES", 80):
            history = authoring._history(messages)
        self.assertIn("new answer", history)
        self.assertIn("Earlier turns omitted", history)
        self.assertNotIn("old-", history)

    def test_only_model_extra_argument_is_accepted(self) -> None:
        self.assertEqual(authoring._safe_extra_args("--model haiku"), ["--model", "haiku"])
        with self.assertRaises(authoring.AuthoringError):
            authoring._safe_extra_args("--allowedTools Write")
        with self.assertRaises(authoring.AuthoringError):
            authoring._safe_extra_args("--dangerously-skip-permissions")

    async def test_prompt_is_sent_on_stdin_with_read_only_tools(self) -> None:
        process = FakeClaudeProcess()
        create = AsyncMock(return_value=process)
        with patch.object(authoring, "CLAUDE_EXTRA_ARGS", "--model haiku"), patch(
            "worker.authoring.asyncio.create_subprocess_exec", create
        ):
            result = await authoring._run("a" * 250_000)
        self.assertEqual(result, "Claude response")
        self.assertEqual(process.input, b"a" * 250_000)
        command = create.await_args.args
        self.assertNotIn("a" * 250_000, command)
        self.assertIn("Read", command)
        self.assertNotIn("Write", command)


class PublicationTests(unittest.TestCase):
    def test_same_publication_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.object(
            publisher, "RAW_SOURCES_DIR", Path(directory) / "raw" / "sources"
        ):
            first = publisher.publish_authoring_article(
                "article-1", "tian_gong", "Guide", "# Guide\n"
            )
            second = publisher.publish_authoring_article(
                "article-1", "tian_gong", "Renamed Guide", "# Guide\n"
            )
            self.assertEqual(first, second)
            with self.assertRaises(FileExistsError):
                publisher.publish_authoring_article(
                    "article-1", "tian_gong", "Guide", "# Changed\n"
                )
            with self.assertRaises(ValueError):
                publisher.publish_authoring_article(
                    "article-2", "../outside", "Guide", "# Guide\n"
                )
            with self.assertRaises(ValueError):
                publisher.publish_authoring_article(
                    "article-2", "tian_gong", "Guide", "   "
                )


class AuthoringQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_queue_is_bounded_and_same_session_uses_same_lock(self) -> None:
        manager = WorkerManager()
        first = {"type": "authoring_chat", "id": "cmd-1", "session_id": "session-1"}
        second = {"type": "authoring_generate", "id": "cmd-2", "session_id": "session-1"}
        self.assertIs(manager._authoring_lock(first), manager._authoring_lock(second))

        for index in range(manager.authoring_queue.maxsize):
            await manager.route_message(
                {"type": "authoring_history", "id": f"cmd-{index}", "session_id": f"s-{index}"}
            )
        await manager.route_message(
            {"type": "authoring_history", "id": "overflow", "session_id": "overflow"}
        )
        rejection = manager.outgoing.get_nowait()
        self.assertEqual(rejection["id"], "overflow")
        self.assertEqual(rejection["status"], "failed")

    async def test_same_session_commands_are_serialized_across_workers(self) -> None:
        manager = WorkerManager()
        active = 0
        maximum = 0

        async def execute(_data: dict) -> None:
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0.01)
            active -= 1

        workers = [
            asyncio.create_task(manager.authoring_worker(1)),
            asyncio.create_task(manager.authoring_worker(2)),
        ]
        try:
            with patch.object(manager, "authoring_command", side_effect=execute):
                await manager.route_message(
                    {"type": "authoring_chat", "id": "one", "session_id": "same"}
                )
                await manager.route_message(
                    {"type": "authoring_chat", "id": "two", "session_id": "same"}
                )
                await manager.authoring_queue.join()
        finally:
            for task in workers:
                task.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
        self.assertEqual(maximum, 1)

    async def test_different_sessions_can_run_in_parallel(self) -> None:
        manager = WorkerManager()
        first_session = "first"
        first_lock = manager._authoring_lock({"session_id": first_session})
        second_session = next(
            f"second-{index}"
            for index in range(1000)
            if manager._authoring_lock({"session_id": f"second-{index}"}) is not first_lock
        )
        active = 0
        maximum = 0

        async def execute(_data: dict) -> None:
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0.02)
            active -= 1

        workers = [
            asyncio.create_task(manager.authoring_worker(1)),
            asyncio.create_task(manager.authoring_worker(2)),
        ]
        try:
            with patch.object(manager, "authoring_command", side_effect=execute):
                await manager.route_message(
                    {"type": "authoring_chat", "id": "first", "session_id": first_session}
                )
                await manager.route_message(
                    {"type": "authoring_chat", "id": "second", "session_id": second_session}
                )
                await manager.authoring_queue.join()
        finally:
            for task in workers:
                task.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
        self.assertEqual(maximum, 2)


if __name__ == "__main__":
    unittest.main()
