#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PIP_INDEX_URL="${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
python3 -m venv .venv-ecs
. .venv-ecs/bin/activate
python3 -m pip install \
  --index-url "$PIP_INDEX_URL" \
  --timeout 60 \
  --retries 10 \
  -r ecs/requirements.txt
[ -f ecs/.env ] || cp ecs/.env.example ecs/.env
mkdir -p ecs-data
printf '\nECS bootstrap complete.\n'
printf '1. Edit %s/ecs/.env\n' "$ROOT"
printf '2. Start with scripts/run_ecs.sh\n'
printf '3. Sign in as admin and change the seeded password at /admin/users\n'
