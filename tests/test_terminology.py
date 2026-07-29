from __future__ import annotations

import pytest

from worker import claude_process, claude_runner
from worker.prompt_security import GuardDecision
from worker.terminology import canonicalize_product_names


def test_known_generated_translations_restore_canonical_names() -> None:
    text = "其他工具包括慧思开物平台和慧思宇宙平台。"

    assert canonicalize_product_names(text) == (
        "其他工具包括Thinkerstudio遥操数采平台和Thinkercosmos平台。"
    )


def test_system_prompt_requires_verbatim_product_names() -> None:
    assert "Never translate, transliterate, localize" in claude_runner.SYSTEM_PROMPT
    assert "Thinkerstudio" in claude_runner.SYSTEM_PROMPT
    assert "Thinkercosmos" in claude_runner.SYSTEM_PROMPT


def test_system_prompt_rejects_political_and_unrelated_questions() -> None:
    assert "Refuse political questions" in claude_runner.SYSTEM_PROMPT
    assert "any unrelated question" in claude_runner.SYSTEM_PROMPT
    assert "AI notice at the end" not in claude_runner.SYSTEM_PROMPT


@pytest.mark.anyio
async def test_streaming_corrects_translated_name_across_chunks(monkeypatch) -> None:
    async def mock_guard(question: str) -> GuardDecision:
        return GuardDecision(blocked=False)

    answer = "工具包括慧思开物平台，可用于遥操。"

    async def mock_run_claude_process_stream(
        prompt, *, team, system_prompt, on_chunk, timeout=None
    ):
        await on_chunk("工具包括慧思", "", 0)
        await on_chunk("开物平台，可用于遥操。", "", 0)
        return answer

    received: list[str] = []

    async def receive_chunk(text: str, thinking: str, thinking_tokens: int) -> None:
        received.append(text)

    monkeypatch.setattr(claude_runner, "guard_user_input", mock_guard)
    monkeypatch.setattr(
        claude_process,
        "run_claude_process_stream",
        mock_run_claude_process_stream,
    )

    result = await claude_runner.run_claude_stream(
        "这是什么工具？",
        team="walker_s2",
        on_chunk=receive_chunk,
    )

    visible = "".join(received)
    assert result == (
        "工具包括Thinkerstudio遥操数采平台，可用于遥操。\n\n"
        + claude_runner.AI_NOTICE_RESPONSES["zh-CN"]
    )
    assert visible == result
    assert "慧思开物" not in visible
