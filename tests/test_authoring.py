from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import worker.authoring as authoring
import worker.claude_process as claude_process
import worker.publisher as publisher
from worker.manager import WorkerManager


class FakeClaudeProcess:
    def __init__(self, output: bytes = b"Claude response") -> None:
        self.returncode = 0
        self.input: bytes | None = None
        self.output = output

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
        self.input = input
        return self.output, b""


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
        self.assertEqual(
            claude_process.safe_model_args("--model haiku"),
            ["--model", "haiku"],
        )
        with self.assertRaises(claude_process.ClaudeProcessError):
            claude_process.safe_model_args("--allowedTools Write")
        with self.assertRaises(claude_process.ClaudeProcessError):
            claude_process.safe_model_args("--dangerously-skip-permissions")
        with self.assertRaises(claude_process.ClaudeProcessError):
            claude_process.safe_model_args("--model haiku --model opus")
        with self.assertRaises(claude_process.ClaudeProcessError):
            claude_process.safe_model_args("--model 'unterminated")

    async def test_prompt_is_sent_on_stdin_with_read_only_tools(self) -> None:
        process = FakeClaudeProcess()
        create = AsyncMock(return_value=process)
        with patch.object(claude_process, "CLAUDE_EXTRA_ARGS", "--model haiku"), patch(
            "worker.claude_process.asyncio.create_subprocess_exec", create
        ):
            result = await authoring._run(
                "a" * 250_000,
                team="tian_gong",
                system_prompt="Private task policy",
            )
        self.assertEqual(result, "Claude response")
        self.assertEqual(process.input, b"a" * 250_000)
        command = create.await_args.args
        self.assertNotIn("a" * 250_000, command)
        self.assertIn("--safe-mode", command)
        self.assertIn("--disable-slash-commands", command)
        self.assertIn("--no-chrome", command)
        self.assertIn("--no-session-persistence", command)
        self.assertEqual(
            command[command.index("--mcp-config") + 1],
            '{"mcpServers":{}}',
        )
        self.assertEqual(command[command.index("--tools") + 1], "Read,Glob,Grep")
        allowed = command[command.index("--allowedTools") + 1]
        self.assertIn("Read(./wiki/**)", allowed)
        self.assertNotIn("Read,Glob,Grep", allowed)
        self.assertIn("Write", command[command.index("--disallowedTools") + 1])
        self.assertNotIn("--allowedTools Write", command)


class PublicationTests(unittest.TestCase):
    def test_same_publication_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch(
            "worker.config.WORKER_ROOT_DIR", Path(directory)
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
