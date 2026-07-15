from __future__ import annotations

import asyncio
import shlex
from collections.abc import Sequence

from worker.config import (
    BASE_DIR,
    CLAUDE_ALLOWED_TOOLS,
    CLAUDE_EXTRA_ARGS,
    CLAUDE_TIMEOUT,
)
from worker.conversation_store import ConversationTurn

GAP_MARKER = "[KNOWLEDGE_GAP]"

LANGUAGE_NAMES = {
    "zh-CN": "Simplified Chinese (简体中文)",
    "zh-TW": "Traditional Chinese (繁體中文)",
    "ko": "Korean (한국어)",
    "ja": "Japanese (日本語)",
    "en": "English",
    "pt": "Portuguese (Português)",
    "ru": "Russian (Русский)",
    "es": "Spanish (Español)",
}

PROMPT = """You are a read-only customer-service knowledge-base assistant.

You are running in a non-interactive server process inside the knowledge-base project directory. You are already authorized to use the read-only tools Read, Glob, and Grep anywhere inside this project. Use those tools silently. Never ask the end user to approve reading files, never say that you need permission, and never describe your retrieval process.

Required retrieval procedure:
1. Read CLAUDE.md for project-specific rules.
2. Read wiki/index.md to understand coverage.
3. Read the relevant Markdown files under wiki/ in depth.
4. Read raw/sources/ only when the wiki lacks necessary detail.
5. Base the final answer on the files you actually read. Do not invent SDK functions, parameters, specifications, or procedures.

Output requirements:
- Answer in {language_name}.
- Output only the final answer; no tool commentary, permission request, preamble, or chain of thought.
- For procedures, troubleshooting, or safety questions, organize the answer as: conclusion, steps, status checks, cautions.
- If the local knowledge base is insufficient, put {gap_marker} on the first line and then briefly state what information is missing in the selected language.

Recent conversation context (for resolving follow-up references only; it is not a factual source):
{history}

Current user question:
{question}
"""


def _history_text(history: Sequence[ConversationTurn]) -> str:
    if not history:
        return "(No previous turns in this conversation.)"
    blocks: list[str] = []
    for turn in history:
        blocks.append(f"User: {turn.question}\nAssistant: {turn.answer}")
    return "\n\n".join(blocks)


async def run_claude(
    question: str,
    *,
    language: str = "zh-CN",
    history: Sequence[ConversationTurn] = (),
) -> str:
    language_name = LANGUAGE_NAMES.get(language, LANGUAGE_NAMES["zh-CN"])
    prompt = PROMPT.format(
        question=question,
        language_name=language_name,
        history=_history_text(history),
        gap_marker=GAP_MARKER,
    )

    command = ["claude", "-p", prompt]
    if CLAUDE_ALLOWED_TOOLS:
        command.extend(["--allowedTools", *CLAUDE_ALLOWED_TOOLS])
    if CLAUDE_EXTRA_ARGS:
        command.extend(shlex.split(CLAUDE_EXTRA_ARGS))

    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(BASE_DIR),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return "[错误] 本地未找到 claude 命令，请执行 `which claude` 检查"

    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=CLAUDE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except asyncio.TimeoutError:
            process.kill()
        return f"[错误] Claude 调用超时 ({CLAUDE_TIMEOUT}s)"

    if process.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()[:800]
        return f"[错误] Claude 执行失败 (code={process.returncode}): {detail}"
    answer = stdout.decode("utf-8", errors="replace").strip()
    return answer or "(空回答)"
