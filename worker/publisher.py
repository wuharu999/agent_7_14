from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from worker.config import (
    SUPPORTED_SOURCE_SUFFIXES,
    TEAM_MAX_EXTRACTED_BYTES,
    TEAM_MAX_FILES,
    get_team_config,
)


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


def check_team_quota(tc, incoming_directory: Path) -> None:
    current_size = 0
    current_files = 0
    if tc.raw_sources_dir.exists():
        for path in tc.raw_sources_dir.rglob("*"):
            if path.is_file():
                current_files += 1
                current_size += path.stat().st_size
                
    incoming_size = 0
    incoming_files = 0
    if incoming_directory.exists():
        for path in incoming_directory.rglob("*"):
            if path.is_file():
                incoming_files += 1
                incoming_size += path.stat().st_size
                
    if current_files + incoming_files > TEAM_MAX_FILES:
        raise ValueError(f"Quota exceeded: Max {TEAM_MAX_FILES} files allowed per team")
    if current_size + incoming_size > TEAM_MAX_EXTRACTED_BYTES:
        raise ValueError(f"Quota exceeded: Max {TEAM_MAX_EXTRACTED_BYTES} bytes allowed per team")


def publish_directory(staged_directory: Path, team: str, upload_id: str) -> tuple[Path, list[str]]:
    tc = get_team_config(team)
    check_team_quota(tc, staged_directory)
    
    safe_upload = safe_segment(upload_id, "upload")
    final_directory = tc.raw_sources_dir / safe_upload
    final_directory.parent.mkdir(parents=True, exist_ok=True)

    if final_directory.exists():
        sources = collect_supported_sources(final_directory)
    else:
        os.rename(staged_directory, final_directory)
        sources = collect_supported_sources(final_directory)

    identities = [
        f"{team}/{path.relative_to(tc.raw_sources_dir).as_posix()}"
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
