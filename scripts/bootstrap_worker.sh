#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PIP_INDEX_URL="${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
python3 -m venv .venv-worker
. .venv-worker/bin/activate
python3 -m pip install \
  --index-url "$PIP_INDEX_URL" \
  --timeout 60 \
  --retries 10 \
  -r worker/requirements.txt
[ -f worker/.env ] || cp worker/.env.example worker/.env
printf '\nWorker bootstrap complete. Edit %s/worker/.env before starting.\n' "$ROOT"
