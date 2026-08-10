#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
[ -f worker/.env ] || { echo "Missing worker/.env"; exit 1; }
PYTHON_BIN="$ROOT/.venv-worker/bin/python"
[ -x "$PYTHON_BIN" ] || PYTHON_BIN="$(command -v python3)"
"$PYTHON_BIN" - <<'PY'
from worker.config import (
    CEREBRAS_API_KEY,
    DEEPSEEK_API_KEY,
    LLM_WIKI_CACHE_FILE,
    LLM_WIKI_QUEUE_FILE,
    LLM_WIKI_RESCAN_AFTER_PUBLISH,
    TRASH_DIR,
    WORKER_ROOT_DIR,
)

print(f"BASE_DIR: {WORKER_ROOT_DIR}")
print("BASE_DIR exists" if WORKER_ROOT_DIR.is_dir() else "BASE_DIR MISSING")
raw_sources = WORKER_ROOT_DIR / "raw" / "sources"
print("raw/sources exists" if raw_sources.is_dir() else "raw/sources will be created")
print(f"Trash: {TRASH_DIR}")
print(f"LLM Wiki queue: {LLM_WIKI_QUEUE_FILE}")
print(f"LLM Wiki cache: {LLM_WIKI_CACHE_FILE}")
print("queue file exists" if LLM_WIKI_QUEUE_FILE.is_file() else "queue file not present yet")
print("cache file exists" if LLM_WIKI_CACHE_FILE.is_file() else "cache file not present yet")
print(f"LLM Wiki rescan after publish: {str(LLM_WIKI_RESCAN_AFTER_PUBLISH).lower()}")
print(f"Cerebras key configured: {str(bool(CEREBRAS_API_KEY)).lower()}")
print(f"DeepSeek fallback key configured: {str(bool(DEEPSEEK_API_KEY)).lower()}")
PY
