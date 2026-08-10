from __future__ import annotations

from pathlib import Path

import pytest

from worker import publisher, qa_api, qa_response
from worker.prompt_security import GuardDecision
from worker.terminology import canonicalize_product_names


def test_known_generated_translations_restore_canonical_names() -> None:
    text = "其他工具包括慧思开物平台和慧思宇宙平台。"

    assert canonicalize_product_names(text) == (
        "其他工具包括Thinkerstudio遥操数采平台和Thinkercosmos平台。"
    )


@pytest.mark.parametrize(
    ("legacy", "canonical"),
    (
        ("天工2.0雷达头版", "天工行者雷达头版"),
        ("TianGong 2.0 Radar Edition", "天工行者雷达头版"),
        ("天工2.0 lite", "天工行者基础版"),
        ("tiangong2.0-LITE", "天工行者基础版"),
        ("天工2.0 Plus", "天工行者无界"),
        ("TianGong_2.0_plus", "天工行者无界"),
        ("天工2.0 Pro", "天工行者无疆"),
        ("TIANGONG 2.0 PRO", "天工行者无疆"),
        ("天工3.0", "天工行者dex"),
        ("TianGong3.0 dex", "天工行者dex"),
        ("天工2.0", "天工行者"),
        ("tiangong 2.0", "天工行者"),
    ),
)
def test_tiangong_legacy_names_are_always_canonicalized(
    legacy: str, canonical: str
) -> None:
    assert canonicalize_product_names(f"型号：{legacy}。") == f"型号：{canonical}。"


def test_tiangong_canonicalization_is_idempotent() -> None:
    canonical = "天工行者、天工行者基础版、天工行者无界、天工行者无疆、天工行者dex"
    assert canonicalize_product_names(canonical) == canonical


def test_source_publication_keeps_original_text_bytes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    staged = tmp_path / "staged"
    staged.mkdir()
    source = staged / "guide.md"
    original = "# 天工2.0 Pro\n原始文档必须保持不变。\n".encode()
    source.write_bytes(original)

    class TeamConfig:
        raw_sources_dir = tmp_path / "raw" / "sources" / "tian_gong"

    monkeypatch.setattr(publisher, "get_team_config", lambda _team: TeamConfig())
    final_directory, _identities = publisher.publish_directory(
        staged, "tian_gong", "upload-1"
    )

    assert (final_directory / "guide.md").read_bytes() == original


def test_system_prompt_requires_verbatim_product_names() -> None:
    assert "Never translate, transliterate, localize" in qa_api.ANSWER_SYSTEM
    assert "Thinkerstudio" in qa_api.ANSWER_SYSTEM
    assert "Thinkercosmos" in qa_api.ANSWER_SYSTEM
    assert "Never output a legacy TianGong 2.0 or TianGong 3.0 name" in (
        qa_api.ANSWER_SYSTEM
    )
    assert "Never search, read, or rely on raw/original" in qa_api.ANSWER_SYSTEM


def test_system_prompt_rejects_political_and_unrelated_questions() -> None:
    assert "political" in qa_api.ANSWER_SYSTEM
    assert "unrelated questions" in qa_api.ANSWER_SYSTEM
    assert "AI notice at the end" not in qa_api.ANSWER_SYSTEM


def test_simplified_chinese_ai_notice_uses_required_product_wording() -> None:
    assert qa_response.AI_NOTICE_RESPONSES["zh-CN"] == (
        "本应用为AI答疑, 请自行辨别内容准确性后参考使用"
    )


@pytest.mark.anyio
async def test_streaming_corrects_translated_name_across_chunks(monkeypatch) -> None:
    answer = "工具包括慧思开物平台，可用于遥操。"

    async def mock_retrieve(*_args, on_token, **_kwargs):
        await on_token("工具包括慧思")
        await on_token("开物平台，可用于遥操。")
        return answer

    received: list[str] = []

    async def receive_chunk(text: str, thinking: str, thinking_tokens: int) -> None:
        received.append(text)

    monkeypatch.setattr(qa_api, "_retrieve_and_stream", mock_retrieve)

    result = await qa_api.run_qa_api_stream(
        "这是什么工具？",
        team="walker_s2",
        on_chunk=receive_chunk,
        guard_decision=GuardDecision(False, "none", "zh-CN"),
    )

    visible = "".join(received)
    assert result == (
        "工具包括Thinkerstudio遥操数采平台，可用于遥操。\n\n"
        + qa_response.AI_NOTICE_RESPONSES["zh-CN"]
    )
    assert visible == result
    assert "慧思开物" not in visible


@pytest.mark.anyio
async def test_streaming_never_exposes_split_tiangong_legacy_name(monkeypatch) -> None:
    answer = "推荐天工2.0 Pro用于该场景。"

    async def mock_retrieve(*_args, on_token, **_kwargs):
        await on_token("推荐天工2.")
        await on_token("0 Pro用于该场景。")
        return answer

    received: list[str] = []

    async def receive_chunk(text: str, _thinking: str, _tokens: int) -> None:
        received.append(text)

    monkeypatch.setattr(qa_api, "_retrieve_and_stream", mock_retrieve)

    result = await qa_api.run_qa_api_stream(
        "请介绍天工2.0 Pro",
        team="tian_gong",
        on_chunk=receive_chunk,
        guard_decision=GuardDecision(False, "none", "zh-CN"),
    )

    visible = "".join(received)
    assert "天工2.0" not in visible
    assert "TianGong" not in visible
    assert "天工行者无疆" in visible
    assert result == visible
