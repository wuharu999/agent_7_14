#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="${WORKER_PROJECT_ROOT:-$HOME/Documents/agent_7_14}"
DOWNLOAD_DIR="${WORKER_DOWNLOAD_DIR:-$HOME/Downloads}"
TMUX_SESSION="${WORKER_TMUX_SESSION:-agent-7-14-worker}"
ECS_HEALTH_URL="${ECS_HEALTH_URL:-http://47.239.12.206:8000/health}"
BACKUP_PARENT="${WORKER_BACKUP_ROOT:-$HOME/agent_7_14-worker-backups}"
START_WORKER="${START_WORKER:-true}"

if [ "$#" -gt 1 ]; then
  echo "Usage: $0 [release.zip]" >&2
  exit 2
fi

if [ "$#" -eq 1 ]; then
  ZIP_PATH="$1"
else
  shopt -s nullglob
  archives=("$DOWNLOAD_DIR"/*.zip)
  shopt -u nullglob
  if [ "${#archives[@]}" -eq 0 ]; then
    echo "No ZIP file found in $DOWNLOAD_DIR" >&2
    exit 1
  fi
  ZIP_PATH="${archives[0]}"
  for candidate in "${archives[@]:1}"; do
    if [ "$candidate" -nt "$ZIP_PATH" ]; then
      ZIP_PATH="$candidate"
    fi
  done
fi

ZIP_PATH="$(realpath "$ZIP_PATH")"
[ -f "$ZIP_PATH" ] || { echo "ZIP not found: $ZIP_PATH" >&2; exit 1; }
[ -d "$PROJECT_ROOT" ] || { echo "Project not found: $PROJECT_ROOT" >&2; exit 1; }
[ -f "$PROJECT_ROOT/worker/.env" ] || {
  echo "Missing protected Worker environment: $PROJECT_ROOT/worker/.env" >&2
  exit 1
}
[ -d "$PROJECT_ROOT/agent1/agent" ] || {
  echo "Missing live LLM Wiki project: $PROJECT_ROOT/agent1/agent" >&2
  exit 1
}

for required in AGENTS.md worker/manager.py worker/file_manager.py scripts/run_worker.sh; do
  if ! unzip -Z1 "$ZIP_PATH" "$required" >/dev/null; then
    echo "Invalid release ZIP; missing $required" >&2
    exit 1
  fi
done

stamp="$(date +%Y%m%d-%H%M%S)"
backup_dir="$BACKUP_PARENT/$stamp"
mkdir -p "$backup_dir"
cp "$PROJECT_ROOT/worker/.env" "$backup_dir/worker.env"
[ ! -f "$PROJECT_ROOT/worker/teams.json" ] || \
  cp "$PROJECT_ROOT/worker/teams.json" "$backup_dir/teams.json"
sha256sum "$ZIP_PATH" > "$backup_dir/release.sha256"

live_items=()
for item in raw wiki .llm-wiki; do
  [ ! -e "$PROJECT_ROOT/agent1/agent/$item" ] || live_items+=("$item")
done
if [ "${#live_items[@]}" -gt 0 ]; then
  tar -C "$PROJECT_ROOT/agent1/agent" \
    -czf "$backup_dir/llm-wiki-live-data.tgz" "${live_items[@]}"
fi

echo "Release: $ZIP_PATH"
echo "Backup:  $backup_dir"

if tmux has-session -t "$TMUX_SESSION" 2>/dev/null; then
  tmux send-keys -t "$TMUX_SESSION" C-c
  sleep 2
  tmux kill-session -t "$TMUX_SESSION" 2>/dev/null || true
fi

staging="$(mktemp -d "$HOME/agent_7_14-worker-staging.XXXXXX")"
cleanup() {
  rm -rf -- "$staging"
}
trap cleanup EXIT
unzip -q "$ZIP_PATH" -d "$staging"

rsync -a \
  --exclude='.git/' \
  --exclude='ecs/.env' \
  --exclude='worker/.env' \
  --exclude='worker/teams.json' \
  --exclude='.venv-ecs/' \
  --exclude='.venv-worker/' \
  --exclude='.venv-dev/' \
  --exclude='ecs-data/' \
  --exclude='agent1/agent/' \
  "$staging/" "$PROJECT_ROOT/"

chmod +x "$PROJECT_ROOT/scripts/"*.sh
chmod 600 "$PROJECT_ROOT/worker/.env"
"$PROJECT_ROOT/scripts/uv_sync.sh" worker
"$PROJECT_ROOT/.venv-worker/bin/python" -m compileall -q \
  "$PROJECT_ROOT/worker" "$PROJECT_ROOT/shared" "$PROJECT_ROOT/scripts"
"$PROJECT_ROOT/scripts/check_worker_machine.sh"

if [ "$START_WORKER" != "true" ]; then
  echo "Worker code deployed; START_WORKER=$START_WORKER, so it remains stopped."
  exit 0
fi

tmux new-session -d -s "$TMUX_SESSION" \
  "cd $(printf '%q' "$PROJECT_ROOT") && ./scripts/run_worker.sh"

for _attempt in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
  health="$(curl -fsS --max-time 5 "$ECS_HEALTH_URL" 2>/dev/null || true)"
  if [[ "$health" == *'"worker_online":true'* ]] || \
     [[ "$health" == *'"worker_online": true'* ]]; then
    echo "$health"
    echo "Worker deployment complete and connected."
    echo "Backup: $backup_dir"
    exit 0
  fi
  sleep 2
done

echo "Worker started, but ECS did not report worker_online=true." >&2
echo "Check tmux session: $TMUX_SESSION" >&2
exit 1
