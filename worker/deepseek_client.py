from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator

from worker.config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    DEEPSEEK_STRUCTURED_RETRIES,
    DEEPSEEK_TIMEOUT,
    DEEPSEEK_TRANSPORT_RETRIES,
)


log = logging.getLogger("worker.deepseek")


class DeepSeekError(RuntimeError):
    """A DeepSeek operation failed without exposing provider response content."""

    def __init__(self, stage: str, *, retryable: bool, category: str) -> None:
        super().__init__(f"DeepSeek request failed during {stage}")
        self.stage = stage
        self.retryable = retryable
        self.category = category


class DeepSeekConfigurationError(DeepSeekError):
    pass


@dataclass(frozen=True)
class DeepSeekResult:
    text: str
    model: str


def _is_retryable(exc: BaseException) -> tuple[bool, str]:
    status = getattr(exc, "status_code", None)
    if status in {401, 403}:
        return False, "authentication"
    if status == 404:
        return False, "model_or_endpoint"
    if status == 429:
        return True, "rate_limit"
    if isinstance(status, int) and status >= 500:
        return True, "provider_server"
    name = type(exc).__name__.casefold()
    if any(token in name for token in ("timeout", "connection", "connect", "network")):
        return True, "network"
    return False, "provider_response"


def _clean_json_text(value: str) -> str:
    cleaned = value.strip()
    if cleaned.startswith("```") and cleaned.endswith("```"):
        cleaned = cleaned.split("\n", 1)[-1][:-3].strip()
    return cleaned


class DeepSeekClient:
    """Tool-free AsyncOpenAI wrapper with bounded retries and schema validation."""

    def __init__(
        self,
        *,
        api_key: str = DEEPSEEK_API_KEY,
        model: str = DEEPSEEK_MODEL,
        base_url: str = DEEPSEEK_BASE_URL,
        timeout: int = DEEPSEEK_TIMEOUT,
        client: Any | None = None,
    ) -> None:
        if not api_key and client is None:
            raise DeepSeekConfigurationError(
                "client initialization", retryable=False, category="missing_api_key"
            )
        self.model = model
        self.timeout = max(1, int(timeout))
        if client is None:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=float(self.timeout),
            )
        self._client = client

    async def _request(
        self,
        *,
        system: str,
        user: str,
        stage: str,
        json_mode: bool,
        max_tokens: int,
        deadline: float | None = None,
    ) -> DeepSeekResult:
        deadline = deadline or (time.monotonic() + self.timeout)
        attempts = DEEPSEEK_TRANSPORT_RETRIES + 1
        for attempt in range(attempts):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DeepSeekError(stage, retryable=True, category="timeout")
            try:
                request: dict[str, Any] = {
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "temperature": 0,
                    "max_tokens": max_tokens,
                    "extra_body": {"thinking": {"type": "disabled"}},
                }
                if json_mode:
                    request["response_format"] = {"type": "json_object"}
                response = await asyncio.wait_for(
                    self._client.chat.completions.create(**request),
                    timeout=remaining,
                )
                choice = response.choices[0] if response.choices else None
                if choice is not None and str(getattr(choice, "finish_reason", "")) == "length":
                    raise DeepSeekError(
                        stage,
                        retryable=True,
                        category="truncated_output",
                    )
                content = choice.message.content if choice is not None else None
                if not content or not str(content).strip():
                    raise DeepSeekError(stage, retryable=True, category="empty_response")
                return DeepSeekResult(str(content), self.model)
            except asyncio.CancelledError:
                raise
            except DeepSeekError:
                raise
            except Exception as exc:
                retryable, category = _is_retryable(exc)
                if not retryable or attempt + 1 >= attempts:
                    raise DeepSeekError(
                        stage, retryable=retryable, category=category
                    ) from exc
                delay = min(2.0, 0.5 * (2**attempt)) + random.uniform(0, 0.1)
                log.warning(
                    "Retrying DeepSeek stage=%s category=%s attempt=%d",
                    stage,
                    category,
                    attempt + 2,
                )
                await asyncio.sleep(min(delay, max(0.0, remaining - 0.1)))
        raise DeepSeekError(stage, retryable=True, category="retry_exhausted")

    async def complete_text(
        self,
        system: str,
        user: str,
        *,
        stage: str,
        max_tokens: int = 8192,
    ) -> str:
        deadline = time.monotonic() + self.timeout
        for attempt in range(DEEPSEEK_STRUCTURED_RETRIES + 1):
            try:
                result = await self._request(
                    system=system,
                    user=user,
                    stage=stage,
                    json_mode=False,
                    max_tokens=max_tokens,
                    deadline=deadline,
                )
                return result.text.strip()
            except DeepSeekError as exc:
                if exc.category != "empty_response" or attempt >= DEEPSEEK_STRUCTURED_RETRIES:
                    raise
        raise DeepSeekError(stage, retryable=True, category="empty_response")

    async def complete_json(
        self,
        system: str,
        user: str,
        *,
        schema: dict[str, Any],
        stage: str,
        max_tokens: int = 16384,
    ) -> dict[str, Any]:
        validator = Draft202012Validator(schema)
        prompt = (
            user.rstrip()
            + "\n\nReturn one complete JSON object only. It must satisfy this JSON Schema:\n"
            + json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
        )
        last_error: BaseException | None = None
        deadline = time.monotonic() + self.timeout
        for structured_attempt in range(DEEPSEEK_STRUCTURED_RETRIES + 1):
            request_prompt = prompt
            if structured_attempt:
                request_prompt += (
                    "\n\nThe previous response was empty, malformed, truncated, or schema-invalid. "
                    "Regenerate the complete JSON object from the original inputs."
                )
            try:
                result = await self._request(
                    system=system,
                    user=request_prompt,
                    stage=stage,
                    json_mode=True,
                    max_tokens=max_tokens,
                    deadline=deadline,
                )
                value = json.loads(_clean_json_text(result.text))
                if not isinstance(value, dict):
                    raise ValueError("structured response is not an object")
                validator.validate(value)
                return value
            except asyncio.CancelledError:
                raise
            except DeepSeekError as exc:
                last_error = exc
                if exc.category not in {"empty_response", "truncated_output"} or structured_attempt >= DEEPSEEK_STRUCTURED_RETRIES:
                    raise
            except Exception as exc:
                last_error = exc
                if structured_attempt >= DEEPSEEK_STRUCTURED_RETRIES:
                    break
        raise DeepSeekError(
            stage, retryable=True, category="structured_validation"
        ) from last_error


def create_deepseek_client(*, timeout: int | None = None) -> DeepSeekClient:
    return DeepSeekClient(timeout=timeout or DEEPSEEK_TIMEOUT)
