import asyncio
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
    type(gw).online = property(lambda self: True)
    
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
