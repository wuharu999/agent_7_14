from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

import worker.deepseek_client as deepseek


def _response(content: str, *, finish_reason: str | None = None):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content),
                finish_reason=finish_reason,
            )
        ]
    )


class FakeCompletions:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class FakeClient:
    def __init__(self, outcomes: list[object]) -> None:
        self.chat = SimpleNamespace(completions=FakeCompletions(outcomes))


class APIConnectionError(Exception):
    pass


class AuthenticationError(Exception):
    status_code = 401


def test_structured_call_disables_thinking_and_repairs_invalid_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeClient([_response("not json"), _response('{"value":"ok"}')])
    monkeypatch.setattr(deepseek, "DEEPSEEK_STRUCTURED_RETRIES", 1)
    client = deepseek.DeepSeekClient(api_key="test-key", client=fake)
    result = asyncio.run(
        client.complete_json(
            "policy",
            "input",
            schema={
                "type": "object",
                "required": ["value"],
                "properties": {"value": {"const": "ok"}},
                "additionalProperties": False,
            },
            stage="test structured operation",
        )
    )
    assert result == {"value": "ok"}
    assert len(fake.chat.completions.calls) == 2
    assert all(
        call["extra_body"] == {"thinking": {"type": "disabled"}}
        for call in fake.chat.completions.calls
    )
    assert all(call["response_format"] == {"type": "json_object"} for call in fake.chat.completions.calls)


def test_structured_call_retries_explicit_truncated_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeClient(
        [
            _response('{"value":"cut', finish_reason="length"),
            _response('{"value":"ok"}', finish_reason="stop"),
        ]
    )
    monkeypatch.setattr(deepseek, "DEEPSEEK_STRUCTURED_RETRIES", 1)
    client = deepseek.DeepSeekClient(api_key="test-key", client=fake)
    result = asyncio.run(
        client.complete_json(
            "policy",
            "input",
            schema={
                "type": "object",
                "required": ["value"],
                "properties": {"value": {"const": "ok"}},
                "additionalProperties": False,
            },
            stage="truncated structured operation",
        )
    )
    assert result == {"value": "ok"}
    assert len(fake.chat.completions.calls) == 2


def test_transient_failure_retries_without_logging_secret(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake = FakeClient([APIConnectionError("secret-key-value"), _response("done")])
    monkeypatch.setattr(deepseek, "DEEPSEEK_TRANSPORT_RETRIES", 1)
    monkeypatch.setattr(deepseek.random, "uniform", lambda *_args: 0)
    client = deepseek.DeepSeekClient(api_key="secret-key-value", client=fake)
    with caplog.at_level(logging.WARNING):
        assert asyncio.run(client.complete_text("policy", "input", stage="network test")) == "done"
    assert len(fake.chat.completions.calls) == 2
    assert "secret-key-value" not in caplog.text


def test_empty_text_is_repaired_once(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeClient([_response(""), _response("repaired")])
    monkeypatch.setattr(deepseek, "DEEPSEEK_STRUCTURED_RETRIES", 1)
    client = deepseek.DeepSeekClient(api_key="test-key", client=fake)
    assert asyncio.run(client.complete_text("policy", "input", stage="text repair")) == "repaired"
    assert len(fake.chat.completions.calls) == 2


def test_permanent_failure_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeClient([AuthenticationError("bad credential")])
    monkeypatch.setattr(deepseek, "DEEPSEEK_TRANSPORT_RETRIES", 3)
    client = deepseek.DeepSeekClient(api_key="test-key", client=fake)
    with pytest.raises(deepseek.DeepSeekError) as raised:
        asyncio.run(client.complete_text("policy", "input", stage="authentication test"))
    assert raised.value.retryable is False
    assert raised.value.category == "authentication"
    assert len(fake.chat.completions.calls) == 1


def test_document_authoring_routes_are_not_registered() -> None:
    from ecs.app.main import app

    assert not any(
        str(getattr(route, "path", "")).startswith("/api/authoring")
        for route in app.routes
    )


def test_no_claude_subprocess_module_or_configuration_remains() -> None:
    root = Path(__file__).resolve().parents[1]
    assert not (root / "worker" / "claude_process.py").exists()
    assert not (root / "worker" / "authoring.py").exists()
    migrated_modules = (
        "capability_matcher.py",
        "prompt_security.py",
        "scenario_clarification.py",
        "manager.py",
    )
    worker_text = "\n".join(
        (root / "worker" / name).read_text(encoding="utf-8")
        for name in migrated_modules
    )
    assert "run_claude" not in worker_text
    assert "CLAUDE_" not in worker_text
