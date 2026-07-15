from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from worker.config import RAW_SOURCES_DIR, SUPPORTED_SOURCE_SUFFIXES


def safe_segment(value: str, default: str) -> str:
    cleaned = (value or default).strip().replace("/", "_").replace("\\", "_")
    cleaned = re.sub(r"[^a-zA-Z0-9._\-\u4e00-\u9fff]+", "_", cleaned)
    return cleaned[:120] or default


def collect_supported_sources(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_SOURCE_SUFFIXES
    )


def publish_directory(staged_directory: Path, team: str, upload_id: str) -> tuple[Path, list[str]]:
    safe_team = safe_segment(team, "default")
    safe_upload = safe_segment(upload_id, "upload")
    final_directory = RAW_SOURCES_DIR / safe_team / safe_upload
    final_directory.parent.mkdir(parents=True, exist_ok=True)

    if final_directory.exists():
        sources = collect_supported_sources(final_directory)
    else:
        os.rename(staged_directory, final_directory)
        sources = collect_supported_sources(final_directory)

    identities = [
        path.relative_to(RAW_SOURCES_DIR).as_posix()
        for path in sources
    ]
    return final_directory, identities


def prepare_single_file(downloaded_file: Path, publish_directory: Path) -> list[Path]:
    if publish_directory.exists():
        shutil.rmtree(publish_directory)
    publish_directory.mkdir(parents=True, exist_ok=True)
    destination = publish_directory / downloaded_file.name
    shutil.copy2(downloaded_file, destination)
    return [destination]
