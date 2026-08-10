#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
[ -f ecs/.env ] || { echo "Missing ecs/.env; run scripts/bootstrap_ecs.sh"; exit 1; }
[ -x .venv-ecs/bin/python ] || { echo "Missing .venv-ecs; run scripts/bootstrap_ecs.sh"; exit 1; }
exec .venv-ecs/bin/python -m uvicorn cloud_app:app --host 0.0.0.0 --port "${PORT:-8000}"
