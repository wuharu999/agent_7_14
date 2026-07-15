#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
[ -f worker/.env ] || { echo "Missing worker/.env"; exit 1; }
set -a; . worker/.env; set +a
printf 'Claude: '; command -v claude || true
printf 'BASE_DIR: %s\n' "$BASE_DIR"
[ -d "$BASE_DIR" ] && echo 'BASE_DIR exists' || echo 'BASE_DIR MISSING'
[ -d "$BASE_DIR/raw/sources" ] && echo 'raw/sources exists' || echo 'raw/sources will be created'
printf 'Trash: %s\n' "${TRASH_DIR:-$BASE_DIR/.agent1-trash}"
printf 'LLM Wiki queue: %s\n' "$LLM_WIKI_QUEUE_FILE"
printf 'LLM Wiki cache: %s\n' "$LLM_WIKI_CACHE_FILE"
[ -f "$LLM_WIKI_QUEUE_FILE" ] && echo 'queue file exists' || echo 'queue file not present yet'
[ -f "$LLM_WIKI_CACHE_FILE" ] && echo 'cache file exists' || echo 'cache file not present yet'
printf 'LLM Wiki rescan after publish: %s\n' "${LLM_WIKI_RESCAN_AFTER_PUBLISH:-false}"
