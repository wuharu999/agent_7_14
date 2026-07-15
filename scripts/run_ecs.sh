#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
[ -f ecs/.env ] || { echo "Missing ecs/.env; run scripts/bootstrap_ecs.sh"; exit 1; }
. .venv-ecs/bin/activate
exec python3 -m uvicorn cloud_app:app --host 0.0.0.0 --port "${PORT:-8000}"
