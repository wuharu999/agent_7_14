#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
[ -f worker/.env ] || { echo "Missing worker/.env; run scripts/bootstrap_worker.sh"; exit 1; }
[ -x .venv-worker/bin/python ] || { echo "Missing .venv-worker; run scripts/bootstrap_worker.sh"; exit 1; }
# shellcheck source=worker_proxy_env.sh
source "$ROOT/scripts/worker_proxy_env.sh"
sanitize_worker_proxy_env
exec .venv-worker/bin/python local_worker.py
