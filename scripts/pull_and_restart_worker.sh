#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

DEPLOY_BRANCH="${DEPLOY_BRANCH:-main}"
TMUX_SESSION="${WORKER_TMUX_SESSION:-agent-7-14-worker}"
BACKUP_PARENT="${WORKER_BACKUP_ROOT:-$PROJECT_ROOT/.agent1-deploy-backups}"
LIVE_PROJECT="$PROJECT_ROOT/agent1/agent"

[ -d .git ] || { echo "Not a Git checkout: $PROJECT_ROOT" >&2; exit 1; }
[ -f worker/.env ] || { echo "Missing worker/.env" >&2; exit 1; }
[ -d "$LIVE_PROJECT" ] || { echo "Missing live LLM Wiki project: $LIVE_PROJECT" >&2; exit 1; }
[ -x .venv-worker/bin/python ] || { echo "Missing .venv-worker" >&2; exit 1; }

stamp="$(date +%Y%m%d-%H%M%S)"
backup_dir="$BACKUP_PARENT/$stamp"
mkdir -p "$backup_dir"
cp worker/.env "$backup_dir/worker.env"
git rev-parse HEAD > "$backup_dir/previous-commit.txt"

live_items=()
for item in CLAUDE.md raw wiki .llm-wiki; do
  [ -e "$LIVE_PROJECT/$item" ] && live_items+=("$item")
done
if [ "${#live_items[@]}" -gt 0 ]; then
  tar -C "$LIVE_PROJECT" -czf "$backup_dir/llm-wiki-live-data.tgz" "${live_items[@]}"
fi

git fetch origin "$DEPLOY_BRANCH"
git merge --ff-only "origin/$DEPLOY_BRANCH"

.venv-worker/bin/python -m pip install -r worker/requirements.txt
.venv-worker/bin/python -m compileall -q worker shared scripts
chmod 600 worker/.env
./scripts/check_worker_machine.sh

if tmux has-session -t "$TMUX_SESSION" 2>/dev/null; then
  tmux send-keys -t "$TMUX_SESSION" C-c || true
  sleep 2
  tmux kill-session -t "$TMUX_SESSION" 2>/dev/null || true
fi
tmux new-session -d -s "$TMUX_SESSION" \
  "cd $(printf '%q' "$PROJECT_ROOT") && ./scripts/run_worker.sh"

sleep 3
tmux has-session -t "$TMUX_SESSION"
echo "Worker deployment complete. Verify worker_online on the ECS /health endpoint."
echo "Backup: $backup_dir"
