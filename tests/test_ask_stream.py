import asyncio
from pathlib import Path

import pytest
from ecs.app.gateway import WorkerGateway
from worker.conversation_store import ConversationTurn

@pytest.mark.anyio
async def test_gateway_ask_stream():
    # Instantiate gateway
    gw = WorkerGateway()

    # Mock online property to return True so send doesn't raise error
    # We will subclass or mock send
    sent_messages = []

    async def mock_send(msg):
        sent_messages.append(msg)

    gw.send = mock_send
    # mock online property
    gw.websocket = object()

    # Start ask_stream as a task
    async def run_stream():
        events = []
        async for event in gw.ask_stream(
            "Hello space",
            team="walker_s2",
            conversation_id="conv_123",
            language="en"
        ):
            events.append(event)
        return events

    task = asyncio.create_task(run_stream())

    # Allow task to run and register its queue
    await asyncio.sleep(0.01)

    # Check that a message was sent and queue exists
    assert len(sent_messages) == 1
    msg = sent_messages[0]
    assert msg["type"] == "question"
    assert msg["text"] == "Hello space"

    qid = msg["id"]
    assert qid in gw.pending_streams

    # Put chunks into the registered queue
    queue = gw.pending_streams[qid]
    await queue.put({"text": "Hello", "status": "chunk"})
    await queue.put({"text": " world", "status": "chunk"})
    await queue.put({"status": "done"})

    # Wait for the task to finish
    events = await task

    assert len(events) == 3
    assert events[0] == {"text": "Hello", "status": "chunk"}
    assert events[1] == {"text": " world", "status": "chunk"}
    assert events[2] == {"status": "done"}


@pytest.mark.anyio
async def test_claude_runner_query_target(monkeypatch):
    from worker import claude_runner
    from worker.prompt_security import GuardDecision

    # Mock guard_user_input to bypass safety check
    async def mock_guard(question):
        return GuardDecision(blocked=False)
    monkeypatch.setattr(claude_runner, "guard_user_input", mock_guard)

    # Mock run_claude_process to verify the prompt contains the target prefix
    received_prompts = []
    async def mock_run_claude_process(prompt, *, team, system_prompt, timeout=None):
        received_prompts.append((prompt, team))
        return "response"
    monkeypatch.setattr(claude_runner, "run_claude_process", mock_run_claude_process)

    # Test specific robot
    await claude_runner.run_claude("What is walker?", team="walker_s2")
    assert len(received_prompts) == 1
    assert "[Query Target: walker_s2] What is walker?" in received_prompts[0][0]

    # Test all robots
    await claude_runner.run_claude("What is walker?", team="all")
    assert len(received_prompts) == 2
    assert "[Query Target: All Robots] What is walker?" in received_prompts[1][0]

    # Test default robot
    await claude_runner.run_claude("What is walker?", team="default")
    assert len(received_prompts) == 3
    assert "[Query Target: All Robots] What is walker?" in received_prompts[2][0]


@pytest.mark.anyio
async def test_claude_runner_hides_internal_chunking_errors(monkeypatch):
    from worker import claude_runner
    from worker.prompt_security import GuardDecision

    async def mock_guard(question):
        return GuardDecision(blocked=False)

    async def mock_run_claude_process(prompt, *, team, system_prompt, timeout=None):
        return "Separator is found, but chunk is longer than limit"

    monkeypatch.setattr(claude_runner, "guard_user_input", mock_guard)
    monkeypatch.setattr(claude_runner, "run_claude_process", mock_run_claude_process)

    answer = await claude_runner.run_claude("你能干啥", team="tian_gong")

    assert answer == "[错误] 助手暂时无法响应，请稍后再试。"


@pytest.mark.anyio
async def test_nonstream_answer_corrects_known_product_translation(monkeypatch):
    from worker import claude_runner
    from worker.prompt_security import GuardDecision

    async def mock_guard(question):
        return GuardDecision(blocked=False)

    async def mock_run_claude_process(prompt, *, team, system_prompt, timeout=None):
        return "平台名称是慧思开物平台。"

    monkeypatch.setattr(claude_runner, "guard_user_input", mock_guard)
    monkeypatch.setattr(claude_runner, "run_claude_process", mock_run_claude_process)

    answer = await claude_runner.run_claude("这是什么平台？", team="walker_s2")

    assert answer == "平台名称是Thinkerstudio遥操数采平台。"


@pytest.mark.anyio
async def test_streaming_hides_internal_chunking_errors(monkeypatch):
    from worker import claude_process, claude_runner
    from worker.prompt_security import GuardDecision

    async def mock_guard(question):
        return GuardDecision(blocked=False)

    async def mock_run_claude_process_stream(
        prompt, *, team, system_prompt, on_chunk, timeout=None
    ):
        await on_chunk("Separator is found, but chunk is longer than limit", "", 0)
        return "Separator is found, but chunk is longer than limit"

    received = []

    async def receive_chunk(text, thinking, thinking_tokens):
        received.append((text, thinking, thinking_tokens))

    monkeypatch.setattr(claude_runner, "guard_user_input", mock_guard)
    monkeypatch.setattr(
        claude_process, "run_claude_process_stream", mock_run_claude_process_stream
    )

    answer = await claude_runner.run_claude_stream(
        "你能干啥",
        team="tian_gong",
        on_chunk=receive_chunk,
    )

    assert answer == "[错误] 助手暂时无法响应，请稍后再试。"
    assert received == [(answer, "", 0)]


@pytest.mark.anyio
async def test_streaming_preserves_safe_text_and_thinking_progress(monkeypatch):
    from worker import claude_process, claude_runner
    from worker.prompt_security import GuardDecision

    async def mock_guard(question):
        return GuardDecision(blocked=False)

    answer = "A" * 100

    async def mock_run_claude_process_stream(
        prompt, *, team, system_prompt, on_chunk, timeout=None
    ):
        await on_chunk(answer[:70], "private hidden reasoning", 12)
        await on_chunk(answer[70:], "", 0)
        return answer

    received = []

    async def receive_chunk(text, thinking, thinking_tokens):
        received.append((text, thinking, thinking_tokens))

    monkeypatch.setattr(claude_runner, "guard_user_input", mock_guard)
    monkeypatch.setattr(
        claude_process, "run_claude_process_stream", mock_run_claude_process_stream
    )

    result = await claude_runner.run_claude_stream(
        "正常问题",
        team="tian_gong",
        on_chunk=receive_chunk,
    )

    assert result == answer
    assert received[0] == ("", "", 12)
    assert "".join(text for text, _, _ in received) == answer
    assert all(thinking == "" for _, thinking, _ in received)
    assert len(received) >= 3


@pytest.mark.anyio
async def test_streaming_hides_wiki_image_marker_from_text(monkeypatch):
    from worker import claude_process, claude_runner
    from worker.prompt_security import GuardDecision

    async def mock_guard(question):
        return GuardDecision(blocked=False)

    answer = "机器人外观如下。\n\n![正面图](wiki/media/manual/robot.png)"

    async def mock_run_claude_process_stream(
        prompt, *, team, system_prompt, on_chunk, timeout=None
    ):
        await on_chunk(answer, "", 0)
        return answer

    received = []

    async def receive_chunk(text, thinking, thinking_tokens):
        received.append((text, thinking, thinking_tokens))

    monkeypatch.setattr(claude_runner, "guard_user_input", mock_guard)
    monkeypatch.setattr(
        claude_process, "run_claude_process_stream", mock_run_claude_process_stream
    )

    result = await claude_runner.run_claude_stream(
        "给我看图片",
        team="tian_gong",
        on_chunk=receive_chunk,
    )

    assert result == answer
    visible_text = "".join(text for text, _, _ in received)
    assert visible_text == "机器人外观如下。"
    assert "wiki/media" not in visible_text


@pytest.mark.anyio
async def test_gateway_nonstream_answer_uses_replacement_text():
    gw = WorkerGateway()
    sent_messages = []

    async def mock_send(msg):
        sent_messages.append(msg)

    gw.send = mock_send
    gw.websocket = object()
    task = asyncio.create_task(
        gw.ask(
            "Show image",
            team="tian_gong",
            conversation_id="conv-image",
            language="en",
        )
    )
    await asyncio.sleep(0.01)
    qid = sent_messages[0]["id"]
    queue = gw.pending_streams[qid]
    await queue.put({"text": "Answer ![img](wiki/media/a.png)", "status": "chunk"})
    await queue.put({"replace_text": "Answer", "status": "chunk"})
    await queue.put({"status": "done"})

    assert await task == "Answer"


def test_qa_pages_render_validated_image_payloads():
    root = Path(__file__).resolve().parents[1]
    for template_name in ("ask.html", "wecom_ask.html"):
        template = (root / "ecs" / "app" / "templates" / template_name).read_text(
            encoding="utf-8"
        )
        assert "event.image.mime_type" in template
        assert "answer-images" in template
        assert "event.replace_text" in template


def test_history_omits_prior_internal_chunking_errors():
    from worker import claude_runner

    history = [
        ConversationTurn(
            question="你能干啥",
            answer="Separator is found, but chunk is longer than limit",
        ),
        ConversationTurn(question="正常问题", answer="正常回答"),
    ]

    rendered = claude_runner._history_text(history)

    assert "Separator is found" not in rendered
    assert "正常问题" in rendered


@pytest.mark.anyio
async def test_predefined_responses():
    from worker import claude_runner

    # Test "Separator is not found"
    resp1 = await claude_runner.run_claude("Separator is not found", team="all")
    assert resp1 == "整个知识库中未出现该错误信息字符串。"

    # Test "chunk exceed the limit"
    resp2 = await claude_runner.run_claude("chunk exceed the limit", team="walker_s2")
    assert resp2 == "整个知识库中未出现该错误信息字符串。"

    # Test "全部机器人"
    resp3 = await claude_runner.run_claude("全部机器人", team="all")
    assert resp3 == "该标记格式未出现在任何 wiki 页面、源文件或代码中。"

    # Test "All Robots" tag
    resp4 = await claude_runner.run_claude("[Query Target: All Robots]", team="all")
    assert resp4 == "该标记格式未出现在任何 wiki 页面、源文件或代码中。"

    expected_by_language = {
        "zh-CN": "该标记格式未出现在任何 wiki 页面、源文件或代码中。",
        "zh-TW": "該標記格式未出現在任何 wiki 頁面、來源檔案或程式碼中。",
        "ko": "해당 태그 형식은 어떤 위키 페이지, 소스 파일 또는 코드에도 없습니다.",
        "ja": "そのタグ形式は、どのWikiページ、ソースファイル、コードにもありません。",
        "en": "That tag format does not appear in any wiki page, source file, or code.",
        "pt": "Esse formato de tag não aparece em nenhuma página wiki, arquivo-fonte ou código.",
        "ru": "Этот формат тега не встречается ни на одной wiki-странице, ни в исходных файлах, ни в коде.",
        "es": "Ese formato de etiqueta no aparece en ninguna página wiki, archivo fuente ni código.",
    }
    for language, expected in expected_by_language.items():
        response = await claude_runner.run_claude(
            "[Query Target: All Robots]", team="all", language=language
        )
        assert response == expected

    english_error = await claude_runner.run_claude(
        "Separator is not found", team="all", language="en"
    )
    assert english_error == (
        "That error message string does not appear anywhere in the knowledge base."
    )
