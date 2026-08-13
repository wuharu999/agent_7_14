from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from dotenv import load_dotenv

from shared.source_types import SUPPORTED_SOURCE_SUFFIXES
from shared.team_names import normalize_team_name

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


def get_allowed_teams() -> list[str]:
    teams = list(ALLOWED_TEAMS)
    raw_sources = WORKER_ROOT_DIR / "raw" / "sources"
    if raw_sources.is_dir():
        for item in raw_sources.iterdir():
            if item.is_dir() and not item.name.startswith("."):
                if item.name not in teams:
                    teams.append(item.name)
    return sorted(teams)
WORKER_ROOT_DIR = Path(os.environ.get("BASE_DIR", str(PROJECT_ROOT / "agent1"))).expanduser().resolve()
LLM_WIKI_QUEUE_FILE = Path(
    os.environ.get(
        "LLM_WIKI_QUEUE_FILE",
        str(WORKER_ROOT_DIR / ".llm-wiki" / "ingest-queue.json"),
    )
).expanduser().resolve()
LLM_WIKI_CACHE_FILE = Path(
    os.environ.get(
        "LLM_WIKI_CACHE_FILE",
        str(WORKER_ROOT_DIR / ".llm-wiki" / "ingest-cache.json"),
    )
).expanduser().resolve()
STAGING_DIR = Path(
    os.environ.get("STAGING_DIR", str(WORKER_ROOT_DIR / ".agent1-worker" / "staging"))
).expanduser().resolve()
TRASH_DIR = Path(
    os.environ.get("TRASH_DIR", str(WORKER_ROOT_DIR / ".agent1-trash"))
).expanduser().resolve()
@dataclass
class TeamConfig:
    team_name: str
    base_dir: Path
    raw_sources_dir: Path
    wiki_dir: Path
    llm_wiki_queue_file: Path
    llm_wiki_cache_file: Path
    llm_wiki_api_url: str

def get_team_config(team: str) -> TeamConfig:
    team = normalize_team_name(team)
    teams_json_path = PROJECT_ROOT / "worker" / "teams.json"
    port = 19828 # default fallback
    if teams_json_path.is_file():
        try:
            teams_data = json.loads(teams_json_path.read_text())
            if team in teams_data and "port" in teams_data[team]:
                port = teams_data[team]["port"]
        except Exception:
            pass

    return TeamConfig(
        team_name=team,
        base_dir=WORKER_ROOT_DIR,
        raw_sources_dir=WORKER_ROOT_DIR / "raw" / "sources" / team,
        wiki_dir=WORKER_ROOT_DIR / "wiki",
        llm_wiki_queue_file=LLM_WIKI_QUEUE_FILE,
        llm_wiki_cache_file=LLM_WIKI_CACHE_FILE,
        llm_wiki_api_url=f"http://127.0.0.1:{port}/api/v1"
    )

QA_WORKERS = int(os.environ.get("QA_WORKERS", "3"))
DOWNLOAD_WORKERS = int(os.environ.get("DOWNLOAD_WORKERS", "2"))
FILE_OPERATION_WORKERS = int(os.environ.get("FILE_OPERATION_WORKERS", "1"))
CAPABILITY_MATCH_WORKERS = max(1, int(os.environ.get("CAPABILITY_MATCH_WORKERS", "1")))
CAPABILITY_MATCH_QUEUE_MAX = max(1, int(os.environ.get("CAPABILITY_MATCH_QUEUE_MAX", "8")))
CLARIFICATION_WORKERS = max(1, int(os.environ.get("CLARIFICATION_WORKERS", "1")))
CLARIFICATION_QUEUE_MAX = max(1, int(os.environ.get("CLARIFICATION_QUEUE_MAX", "16")))
CAPABILITY_CATALOG_WORKERS = max(
    1, int(os.environ.get("CAPABILITY_CATALOG_WORKERS", "2"))
)
CAPABILITY_CATALOG_QUEUE_MAX = max(
    1, int(os.environ.get("CAPABILITY_CATALOG_QUEUE_MAX", "8"))
)
CAPABILITY_CATALOG_BATCH_CONCURRENCY = max(
    1, int(os.environ.get("CAPABILITY_CATALOG_BATCH_CONCURRENCY", "4"))
)
FILE_MANAGER_MAX_ENTRIES = int(os.environ.get("FILE_MANAGER_MAX_ENTRIES", "10000"))
CEREBRAS_API_KEY = os.environ.get("CEREBRAS_API_KEY", "").strip()
CEREBRAS_MODEL = os.environ.get("CEREBRAS_MODEL", "gpt-oss-120b").strip() or "gpt-oss-120b"
CEREBRAS_TIMEOUT = max(1, int(os.environ.get("CEREBRAS_TIMEOUT", "240")))
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_MODEL = (
    os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash").strip()
    or "deepseek-v4-flash"
)
DEEPSEEK_BASE_URL = (
    os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip()
    or "https://api.deepseek.com"
)
DEEPSEEK_TIMEOUT = max(1, int(os.environ.get("DEEPSEEK_TIMEOUT", "240")))
DEEPSEEK_STRUCTURED_RETRIES = max(
    0, min(2, int(os.environ.get("DEEPSEEK_STRUCTURED_RETRIES", "1")))
)
DEEPSEEK_TRANSPORT_RETRIES = max(
    0, min(3, int(os.environ.get("DEEPSEEK_TRANSPORT_RETRIES", "1")))
)
QA_PROVIDER_COOLDOWN_SECONDS = max(
    1, int(os.environ.get("QA_PROVIDER_COOLDOWN_SECONDS", "300"))
)
CEREBRAS_REGION_CHECK_URL = (
    os.environ.get(
        "CEREBRAS_REGION_CHECK_URL", "https://www.cloudflare.com/cdn-cgi/trace"
    ).strip()
    or "https://www.cloudflare.com/cdn-cgi/trace"
)
CEREBRAS_REGION_CHECK_TIMEOUT = max(
    1, min(30, int(os.environ.get("CEREBRAS_REGION_CHECK_TIMEOUT", "5")))
)
CEREBRAS_REGION_CACHE_SECONDS = max(
    30, int(os.environ.get("CEREBRAS_REGION_CACHE_SECONDS", "300"))
)
CEREBRAS_BLOCKED_COUNTRIES = frozenset(
    value.strip().upper()
    for value in os.environ.get("CEREBRAS_BLOCKED_COUNTRIES", "CN,TW,HK,SG").split(",")
    if value.strip()
)
WIKI_QA_MAX_PAGES = max(1, min(10, int(os.environ.get("WIKI_QA_MAX_PAGES", "8"))))
WIKI_QA_MAX_PAGE_CHARS = max(
    1_000, int(os.environ.get("WIKI_QA_MAX_PAGE_CHARS", "24000"))
)
CAPABILITY_CATALOG_BATCH_BYTES = max(
    16 * 1024,
    int(os.environ.get("CAPABILITY_CATALOG_BATCH_BYTES", str(96 * 1024))),
)
CAPABILITY_CATALOG_UNIT_BYTES = max(
    8 * 1024,
    min(
        CAPABILITY_CATALOG_BATCH_BYTES,
        int(os.environ.get("CAPABILITY_CATALOG_UNIT_BYTES", str(64 * 1024))),
    ),
)
CAPABILITY_CATALOG_BATCH_TIMEOUT = max(
    DEEPSEEK_TIMEOUT,
    1800,
    int(os.environ.get("CAPABILITY_CATALOG_BATCH_TIMEOUT", "1800")),
)
CAPABILITY_CATALOG_REDUCE_TIMEOUT = max(
    DEEPSEEK_TIMEOUT,
    3600,
    int(os.environ.get("CAPABILITY_CATALOG_REDUCE_TIMEOUT", "3600")),
)
SCENARIO_RETRIEVAL_CACHE_FILE = Path(
    os.environ.get(
        "SCENARIO_RETRIEVAL_CACHE_FILE",
        str(WORKER_ROOT_DIR / ".agent1-worker" / "scenario-retrieval.sqlite3"),
    )
).expanduser().resolve()
SCENARIO_RETRIEVAL_CANDIDATES = max(
    10, min(100, int(os.environ.get("SCENARIO_RETRIEVAL_CANDIDATES", "40")))
)
SCENARIO_RETRIEVAL_MAX_DOCUMENTS = max(
    1, min(20, int(os.environ.get("SCENARIO_RETRIEVAL_MAX_DOCUMENTS", "12")))
)
SCENARIO_RETRIEVAL_MAX_PAGE_CHARS = max(
    2_000, int(os.environ.get("SCENARIO_RETRIEVAL_MAX_PAGE_CHARS", "24000"))
)
SCENARIO_RETRIEVAL_MAX_TOTAL_CHARS = max(
    SCENARIO_RETRIEVAL_MAX_PAGE_CHARS,
    int(os.environ.get("SCENARIO_RETRIEVAL_MAX_TOTAL_CHARS", "120000")),
)
CONVERSATION_MAX_TURNS = int(os.environ.get("CONVERSATION_MAX_TURNS", "6"))
CONVERSATION_MAX_SESSIONS = int(os.environ.get("CONVERSATION_MAX_SESSIONS", "1000"))
PROMPT_GUARD_ENABLED = os.environ.get("PROMPT_GUARD_ENABLED", "true").strip().lower() in {
    "1", "true", "yes", "on"
}
PROMPT_GUARD_TIMEOUT = max(1, int(os.environ.get("PROMPT_GUARD_TIMEOUT", "20")))
PROMPT_GUARD_CONCURRENCY = max(
    1, int(os.environ.get("PROMPT_GUARD_CONCURRENCY", "2"))
)
PROMPT_SCAN_MAX_FILE_BYTES = max(1, int(
    os.environ.get("PROMPT_SCAN_MAX_FILE_BYTES", str(2 * 1024**2))
))
PROMPT_SCAN_MAX_TOTAL_BYTES = max(PROMPT_SCAN_MAX_FILE_BYTES, int(
    os.environ.get("PROMPT_SCAN_MAX_TOTAL_BYTES", str(10 * 1024**2))
))
PROMPT_SCAN_MAX_WARNINGS = max(
    1, int(os.environ.get("PROMPT_SCAN_MAX_WARNINGS", "1000"))
)
DOWNLOAD_TIMEOUT = int(os.environ.get("DOWNLOAD_TIMEOUT", "1800"))
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(5 * 1024**3)))
MAX_ZIP_FILES = int(os.environ.get("MAX_ZIP_FILES", "20000"))
MAX_ZIP_EXTRACTED_BYTES = int(os.environ.get("MAX_ZIP_EXTRACTED_BYTES", str(10 * 1024**3)))
MAX_ZIP_SINGLE_FILE_BYTES = int(os.environ.get("MAX_ZIP_SINGLE_FILE_BYTES", str(2 * 1024**3)))
TEAM_MAX_EXTRACTED_BYTES = int(os.environ.get("TEAM_MAX_EXTRACTED_BYTES", str(50 * 1024**3)))
TEAM_MAX_FILES = int(os.environ.get("TEAM_MAX_FILES", "100000"))

LLM_WIKI_POLL_SECONDS = float(os.environ.get("LLM_WIKI_POLL_SECONDS", "2"))
LLM_WIKI_MONITOR_TIMEOUT = int(os.environ.get("LLM_WIKI_MONITOR_TIMEOUT", "7200"))
LLM_WIKI_API_TOKEN = os.environ.get("LLM_WIKI_API_TOKEN", "")
LLM_WIKI_PROJECT_ID = os.environ.get("LLM_WIKI_PROJECT_ID", "")
LLM_WIKI_RESCAN_AFTER_PUBLISH = os.environ.get(
    "LLM_WIKI_RESCAN_AFTER_PUBLISH", "false"
).strip().lower() in {"1", "true", "yes", "on"}

def websocket_url() -> str:
    parts = urlsplit(SERVER_URL)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    # Authentication is sent in an HTTP header so access logs never contain
    # the shared secret. Remove a legacy secret parameter if one was configured.
    query.pop("secret", None)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def http_base_url() -> str:
    parts = urlsplit(SERVER_URL)
    scheme = "https" if parts.scheme in ("wss", "https") else "http"
    return f"{scheme}://{parts.netloc}"



def ensure_directories() -> None:
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    TRASH_DIR.mkdir(parents=True, exist_ok=True)
    (WORKER_ROOT_DIR / "raw" / "sources").mkdir(parents=True, exist_ok=True)
    (WORKER_ROOT_DIR / "wiki").mkdir(parents=True, exist_ok=True)
    LLM_WIKI_QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    LLM_WIKI_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
