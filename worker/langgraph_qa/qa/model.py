from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Sequence, TypeVar

from pydantic import BaseModel

from worker.langgraph_qa.runtime import get_runtime


SchemaT = TypeVar("SchemaT", bound=BaseModel)


@dataclass(frozen=True)
class ModelResponse:
    content: str


def _message_parts(messages: Sequence[Any]) -> tuple[str, str]:
    system_parts: list[str] = []
    user_parts: list[str] = []
    for message in messages:
        content = str(getattr(message, "content", ""))
        role = str(getattr(message, "type", getattr(message, "role", "user")))
        if role == "system":
            system_parts.append(content)
        else:
            user_parts.append(content)
    return "\n\n".join(system_parts), "\n\n".join(user_parts)


def _json_object(value: str) -> dict[str, Any]:
    cleaned = value.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(
            r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE
        )
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("Structured model response did not contain a JSON object")
    parsed = json.loads(cleaned[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("Structured model response was not an object")
    return parsed


class StructuredModel:
    def __init__(self, schema: type[SchemaT]):
        self.schema = schema

    def invoke(self, messages: Sequence[Any]) -> SchemaT:
        system, user = _message_parts(messages)
        schema_json = json.dumps(self.schema.model_json_schema(), ensure_ascii=False)
        raw = get_runtime().provider.complete(
            system + "\nReturn JSON only and match this JSON Schema exactly:\n" + schema_json,
            user,
        )
        return self.schema.model_validate(_json_object(raw))


class ChatModel:
    def with_structured_output(
        self, schema: type[SchemaT], method: str | None = None
    ) -> StructuredModel:
        del method
        return StructuredModel(schema)

    def invoke(self, messages: Sequence[Any]) -> ModelResponse:
        system, user = _message_parts(messages)
        return ModelResponse(get_runtime().provider.complete(system, user))


def get_chat_model() -> ChatModel:
    return ChatModel()
