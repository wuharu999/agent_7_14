#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
: "${WORKER_SSH_TARGET:?Set WORKER_SSH_TARGET, for example user@worker-host}"
REMOTE_ROOT="${WORKER_REMOTE_ROOT:-__REMOTE_HOME__/Documents/agent_7_14}"
REMOTE_TMP="${WORKER_REMOTE_TMP:-/tmp/agent_7_14-release.zip}"
REMOTE_SESSION="${WORKER_TMUX_SESSION:-agent-7-14-worker}"
START_WORKER="${START_WORKER:-true}"

python3 scripts/pack_release.py
echo "Uploading release.zip to ${WORKER_SSH_TARGET}:${REMOTE_TMP}"
scp release.zip "${WORKER_SSH_TARGET}:${REMOTE_TMP}"

ssh "$WORKER_SSH_TARGET" \
  "REMOTE_ROOT=$(printf '%q' "$REMOTE_ROOT") REMOTE_TMP=$(printf '%q' "$REMOTE_TMP") REMOTE_SESSION=$(printf '%q' "$REMOTE_SESSION") START_WORKER=$(printf '%q' "$START_WORKER") bash -s" <<'REMOTE_SCRIPT'
set -euo pipefail

REMOTE_ROOT="${REMOTE_ROOT/__REMOTE_HOME__/$HOME}"
mkdir -p "$REMOTE_ROOT"
if [ ! -f "$REMOTE_ROOT/worker/.env" ]; then
  echo "Missing $REMOTE_ROOT/worker/.env; refusing deployment" >&2
  exit 1
fi

timestamp="$(date +%Y%m%d-%H%M%S)"
backup_root="$REMOTE_ROOT/.agent1-deploy-backups/$timestamp"
mkdir -p "$backup_root"
cp "$REMOTE_ROOT/worker/.env" "$backup_root/worker.env"
if [ -d "$REMOTE_ROOT/agent1/agent" ]; then
  tar -C "$REMOTE_ROOT/agent1" \
    --exclude='agent/raw' --exclude='agent/wiki' --exclude='agent/.llm-wiki' \
    --exclude='agent/.agent1-trash' --exclude='agent/.agent1-worker' \
    -czf "$backup_root/llm-wiki-project.tgz" agent
fi

if tmux has-session -t "$REMOTE_SESSION" 2>/dev/null; then
  tmux send-keys -t "$REMOTE_SESSION" C-c || true
  sleep 2
  tmux kill-session -t "$REMOTE_SESSION" 2>/dev/null || true
fi

staging="$(mktemp -d "$REMOTE_ROOT/.agent1-release.XXXXXX")"
cleanup() { rm -rf "$staging"; rm -f "$REMOTE_TMP"; }
trap cleanup EXIT
unzip -q "$REMOTE_TMP" -d "$staging"

rsync -a --delete \
  --exclude='ecs/.env' --exclude='worker/.env' \
  --exclude='.venv-ecs/' --exclude='.venv-worker/' --exclude='.venv-dev/' \
  --exclude='ecs-data/' \
  --exclude='agent1/agent/raw/' --exclude='agent1/agent/wiki/' \
  --exclude='agent1/agent/.llm-wiki/' --exclude='agent1/agent/.agent1-trash/' \
  --exclude='agent1/agent/.agent1-worker/' \
  "$staging/" "$REMOTE_ROOT/"

chmod +x "$REMOTE_ROOT/scripts/"*.sh
chmod 600 "$REMOTE_ROOT/worker/.env"
"$REMOTE_ROOT/scripts/uv_sync.sh" worker
"$REMOTE_ROOT/.venv-worker/bin/python" -m compileall -q \
  "$REMOTE_ROOT/worker" "$REMOTE_ROOT/shared" "$REMOTE_ROOT/scripts"
"$REMOTE_ROOT/scripts/check_worker_machine.sh"
if [ "$START_WORKER" = true ]; then
  tmux new-session -d -s "$REMOTE_SESSION" \
    "cd $(printf '%q' "$REMOTE_ROOT") && ./scripts/run_worker.sh"
  echo "Worker started in tmux session $REMOTE_SESSION"
else
  echo "Worker code deployed; START_WORKER=false, so it remains stopped"
fi
echo "Backup: $backup_root"
REMOTE_SCRIPT

echo "Worker deployment complete."
