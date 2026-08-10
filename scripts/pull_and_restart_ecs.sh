#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

DEPLOY_BRANCH="${DEPLOY_BRANCH:-main}"
TMUX_SESSION="${ECS_TMUX_SESSION:-agent-7-14-ecs}"
HEALTH_URL="${ECS_HEALTH_URL:-http://127.0.0.1:8000/health}"
BACKUP_PARENT="${ECS_BACKUP_ROOT:-/root/agent_7_14-deploy-backups}"

[ -d .git ] || { echo "Not a Git checkout: $PROJECT_ROOT" >&2; exit 1; }
[ -f ecs/.env ] || { echo "Missing ecs/.env" >&2; exit 1; }
[ -d ecs-data ] || { echo "Missing ecs-data" >&2; exit 1; }

stamp="$(date +%Y%m%d-%H%M%S)"
backup_dir="$BACKUP_PARENT/$stamp"
mkdir -p "$backup_dir"
cp ecs/.env "$backup_dir/ecs.env"
tar -czf "$backup_dir/ecs-data.tgz" ecs-data
git rev-parse HEAD > "$backup_dir/previous-commit.txt"

git fetch origin "$DEPLOY_BRANCH"
git merge --ff-only FETCH_HEAD

./scripts/uv_sync.sh ecs
.venv-ecs/bin/python -m compileall -q ecs shared scripts
chmod 600 ecs/.env

if tmux has-session -t "$TMUX_SESSION" 2>/dev/null; then
  tmux send-keys -t "$TMUX_SESSION" C-c || true
  sleep 2
  tmux kill-session -t "$TMUX_SESSION" 2>/dev/null || true
fi
tmux new-session -d -s "$TMUX_SESSION" \
  "cd $(printf '%q' "$PROJECT_ROOT") && ./scripts/run_ecs.sh"

for _attempt in 1 2 3 4 5 6 7 8 9 10; do
  if curl -fsS --max-time 5 "$HEALTH_URL"; then
    printf '\nECS deployment complete. Backup: %s\n' "$backup_dir"
    exit 0
  fi
  sleep 2
done

echo "ECS did not become healthy; inspect tmux session $TMUX_SESSION" >&2
exit 1
