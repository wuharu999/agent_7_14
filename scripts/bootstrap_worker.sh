#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
./scripts/uv_sync.sh worker
[ -f worker/.env ] || cp worker/.env.example worker/.env
printf '\nWorker bootstrap complete. Edit %s/worker/.env before starting.\n' "$ROOT"
