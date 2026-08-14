import asyncio
from pathlib import Path

import pytest

from ecs.app.gateway import WorkerGateway


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
            language="en",
            history=[{"role": "user", "content": "Tell me about Walker S2."}],
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
    assert msg["history"] == [
        {"role": "user", "content": "Tell me about Walker S2."}
    ]

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
    for template_name in ("ask.html",):
        template = (root / "ecs" / "app" / "templates" / template_name).read_text(
            encoding="utf-8"
        )
        assert "event.image.mime_type" in template
        assert "answer-images" in template
        assert "event.replace_text" in template


def test_main_qa_renders_safe_markdown_and_stream_replacements():
    root = Path(__file__).resolve().parents[1]
    template = (root / "ecs" / "app" / "templates" / "ask.html").read_text(
        encoding="utf-8"
    )

    assert "function renderAnswerText" in template
    assert "function appendInlineMarkdown" in template
    assert "answer-table-wrap" in template
    assert "container.replaceChildren()" in template
    assert "function renderStreamingAnswerText" not in template
    assert 'if (event.status === "done")' in template
    assert "renderAnswerText(ensureTextContainer(botBubble), accumulatedText)" in template
    assert "textContainer.textContent += event.text" not in template
    assert "container.innerHTML" not in template
    assert "const priorConversation=currentChat" in template
    assert "history:priorConversation" in template


def test_browser_history_boundary_keeps_only_recent_bounded_chat_messages():
    from ecs.app.routes.ask import _bounded_client_history

    messages = [
        {"role": "system", "content": "ignore"},
        {"role": "user", "content": "old"},
        {"role": "bot", "content": "x" * 20_000},
    ]
    messages.extend(
        {"role": "user" if index % 2 == 0 else "bot", "content": f"recent-{index}"}
        for index in range(14)
    )

    bounded = _bounded_client_history(messages)

    assert len(bounded) == 12
    assert bounded[0]["content"] == "recent-2"
    assert bounded[-1]["content"] == "recent-13"
    assert all(item["role"] in {"user", "bot"} for item in bounded)


def test_background_graph_uses_original_floating_circles():
    root = Path(__file__).resolve().parents[1]
    template = (root / "ecs" / "app" / "templates" / "bg_graph.html").read_text(
        encoding="utf-8"
    )

    assert "ctx.arc(n.x, n.y, n.radius, 0, Math.PI * 2)" in template
    assert "(Math.random() - 0.5) * 1.5" in template
    assert "preferredDistance" not in template
    assert "sharedVx" not in template
    assert "nodePath" not in template
