from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / "worker" / ".env")

SERVER_URL = os.environ.get("SERVER_URL", "ws://127.0.0.1:8000/ws/client")
WORKER_SHARED_SECRET = os.environ.get("WORKER_SHARED_SECRET", "")
ALLOWED_TEAMS = tuple(
    team.strip()
    for team in os.environ.get(
        "ALLOWED_TEAMS", "tian_gong,walker_s2,walker_c1"
    ).split(",")
    if team.strip()
)
BASE_DIR = Path(os.environ.get("BASE_DIR", str(PROJECT_ROOT / "agent1"))).expanduser().resolve()
RAW_SOURCES_DIR = BASE_DIR / "raw" / "sources"
WIKI_DIR = BASE_DIR / "wiki"
UNANSWERED_FILE = WIKI_DIR / "unanswered.md"
STAGING_DIR = Path(
    os.environ.get("STAGING_DIR", str(BASE_DIR / ".agent1-worker" / "staging"))
).expanduser().resolve()
TRASH_DIR = Path(
    os.environ.get("TRASH_DIR", str(BASE_DIR / ".agent1-trash"))
).expanduser().resolve()
AUTHORING_DIR = Path(
    os.environ.get("AUTHORING_DIR", str(BASE_DIR / ".agent1-worker" / "authoring"))
).expanduser().resolve()

QA_WORKERS = int(os.environ.get("QA_WORKERS", "3"))
DOWNLOAD_WORKERS = int(os.environ.get("DOWNLOAD_WORKERS", "2"))
FILE_OPERATION_WORKERS = int(os.environ.get("FILE_OPERATION_WORKERS", "1"))
AUTHORING_WORKERS = max(1, int(os.environ.get("AUTHORING_WORKERS", "2")))
AUTHORING_QUEUE_MAX = max(1, int(os.environ.get("AUTHORING_QUEUE_MAX", "8")))
AUTHORING_LOCK_STRIPES = max(1, int(os.environ.get("AUTHORING_LOCK_STRIPES", "64")))
FILE_MANAGER_MAX_ENTRIES = int(os.environ.get("FILE_MANAGER_MAX_ENTRIES", "10000"))
CLAUDE_TIMEOUT = int(os.environ.get("CLAUDE_TIMEOUT", "240"))
CLAUDE_ALLOWED_TOOLS = tuple(
    item.strip()
    for item in os.environ.get("CLAUDE_ALLOWED_TOOLS", "Read,Glob,Grep").split(",")
    if item.strip()
)
CLAUDE_EXTRA_ARGS = os.environ.get("CLAUDE_EXTRA_ARGS", "").strip()
CONVERSATION_MAX_TURNS = int(os.environ.get("CONVERSATION_MAX_TURNS", "6"))
CONVERSATION_MAX_SESSIONS = int(os.environ.get("CONVERSATION_MAX_SESSIONS", "1000"))
AUTHORING_MAX_TURNS = int(os.environ.get("AUTHORING_MAX_TURNS", "100"))
AUTHORING_MAX_MESSAGE_BYTES = int(os.environ.get("AUTHORING_MAX_MESSAGE_BYTES", str(50 * 1024)))
AUTHORING_MAX_ARTICLE_BYTES = int(os.environ.get("AUTHORING_MAX_ARTICLE_BYTES", str(500 * 1024)))
AUTHORING_MAX_CONTEXT_BYTES = int(os.environ.get("AUTHORING_MAX_CONTEXT_BYTES", str(750 * 1024)))
DOWNLOAD_TIMEOUT = int(os.environ.get("DOWNLOAD_TIMEOUT", "1800"))
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(5 * 1024**3)))
MAX_ZIP_FILES = int(os.environ.get("MAX_ZIP_FILES", "20000"))
MAX_ZIP_EXTRACTED_BYTES = int(os.environ.get("MAX_ZIP_EXTRACTED_BYTES", str(10 * 1024**3)))
MAX_ZIP_SINGLE_FILE_BYTES = int(os.environ.get("MAX_ZIP_SINGLE_FILE_BYTES", str(2 * 1024**3)))

LLM_WIKI_QUEUE_FILE = Path(
    os.environ.get("LLM_WIKI_QUEUE_FILE", str(BASE_DIR / ".llm-wiki" / "ingest-queue.json"))
).expanduser().resolve()
LLM_WIKI_CACHE_FILE = Path(
    os.environ.get("LLM_WIKI_CACHE_FILE", str(BASE_DIR / ".llm-wiki" / "ingest-cache.json"))
).expanduser().resolve()
LLM_WIKI_POLL_SECONDS = float(os.environ.get("LLM_WIKI_POLL_SECONDS", "2"))
LLM_WIKI_MONITOR_TIMEOUT = int(os.environ.get("LLM_WIKI_MONITOR_TIMEOUT", "7200"))
LLM_WIKI_API_URL = os.environ.get("LLM_WIKI_API_URL", "http://127.0.0.1:19828/api/v1").rstrip("/")
LLM_WIKI_API_TOKEN = os.environ.get("LLM_WIKI_API_TOKEN", "")
LLM_WIKI_PROJECT_ID = os.environ.get("LLM_WIKI_PROJECT_ID", "")
LLM_WIKI_RESCAN_AFTER_PUBLISH = os.environ.get(
    "LLM_WIKI_RESCAN_AFTER_PUBLISH", "false"
).strip().lower() in {"1", "true", "yes", "on"}

SUPPORTED_SOURCE_SUFFIXES = {
    ".pdf", ".docx", ".pptx", ".xlsx", ".md", ".markdown", ".txt",
    ".csv", ".json", ".html", ".htm", ".xml", ".yaml", ".yml",
}


def websocket_url() -> str:
    parts = urlsplit(SERVER_URL)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["secret"] = WORKER_SHARED_SECRET
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def ensure_directories() -> None:
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    TRASH_DIR.mkdir(parents=True, exist_ok=True)
    RAW_SOURCES_DIR.mkdir(parents=True, exist_ok=True)
    WIKI_DIR.mkdir(parents=True, exist_ok=True)
    AUTHORING_DIR.mkdir(parents=True, exist_ok=True)
