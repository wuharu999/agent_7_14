#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
[ -f worker/.env ] || { echo "Missing worker/.env; run scripts/bootstrap_worker.sh"; exit 1; }
. .venv-worker/bin/activate
exec python3 local_worker.py
