from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from worker.config import (
    ALLOWED_TEAMS,
    AUTHORING_MAX_ARTICLE_BYTES,
    SUPPORTED_SOURCE_SUFFIXES,
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


def publish_directory(staged_directory: Path, team: str, upload_id: str) -> tuple[Path, list[str]]:
    tc = get_team_config(team)
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


def publish_authoring_article(article_id: str, team: str, title: str, markdown: str) -> str:
    if team not in ALLOWED_TEAMS:
        raise ValueError("Invalid authoring team")
    if not markdown.strip():
        raise ValueError("Article Markdown cannot be empty")
    if len(markdown.encode("utf-8")) > AUTHORING_MAX_ARTICLE_BYTES:
        raise ValueError("Article Markdown is too large")
    
    tc = get_team_config(team)
    safe_article = safe_segment(article_id, "article")
    safe_title = safe_segment(title, "article")
    final_directory = tc.raw_sources_dir / safe_article
    final_directory.mkdir(parents=True, exist_ok=True)
    existing_articles = sorted(final_directory.glob("*.md"))
    if existing_articles:
        if (
            len(existing_articles) == 1
            and existing_articles[0].read_text(encoding="utf-8") == markdown
        ):
            return f"{team}/{existing_articles[0].relative_to(tc.raw_sources_dir).as_posix()}"
        raise FileExistsError(
            "The article was already published with different content"
        )
    target = final_directory / f"{safe_title[:160]}.md"
    temporary = final_directory / f".{safe_title[:160]}.{article_id}.tmp"
    temporary.write_text(markdown, encoding="utf-8")
    try:
        os.replace(temporary, target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return f"{team}/{target.relative_to(tc.raw_sources_dir).as_posix()}"
