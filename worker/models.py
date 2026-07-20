from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class QuestionJob:
    job_id: str
    question: str
    team: str
    conversation_id: str
    language: str
    stream: bool = False


@dataclass(slots=True)
class DownloadJob:
    task_id: str
    upload_id: str
    team: str
    filename: str
    download_url: str
    published_at_ms: int = 0


@dataclass(slots=True)
class FileOperationJob:
    command_id: str
    operation: str
    payload: dict[str, Any]
