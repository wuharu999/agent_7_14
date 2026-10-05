#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"
if [ -f .env ]; then
  set -a
  source .env
  set +a
fi
exec .venv/bin/python -m uvicorn backend.app:app --host "${BIND_HOST:-0.0.0.0}" --port "${PORT:-8080}"
