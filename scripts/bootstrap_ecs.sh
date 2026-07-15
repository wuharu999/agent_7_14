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
printf '2. Create the first admin:\n   source .venv-ecs/bin/activate && python3 scripts/create_user.py --username admin --role admin\n'
printf '3. Start with scripts/run_ecs.sh\n'
