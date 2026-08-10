#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

target="${1:-}"
case "$target" in
  ecs)
    environment="$PROJECT_ROOT/.venv-ecs"
    extras=(--extra ecs --no-dev)
    ;;
  worker)
    environment="$PROJECT_ROOT/.venv-worker"
    extras=(--extra worker --no-dev)
    ;;
  dev)
    environment="$PROJECT_ROOT/.venv-dev"
    extras=(--extra ecs --extra worker)
    ;;
  *)
    echo "Usage: $0 {ecs|worker|dev}" >&2
    exit 2
    ;;
esac

if [ -n "${UV_BIN:-}" ]; then
  uv_bin="$UV_BIN"
elif command -v uv >/dev/null 2>&1; then
  uv_bin="$(command -v uv)"
elif [ -x "$HOME/.local/bin/uv" ]; then
  uv_bin="$HOME/.local/bin/uv"
else
  echo "uv is required. Install it from https://docs.astral.sh/uv/getting-started/installation/" >&2
  exit 1
fi

if [ -x "$environment/bin/python" ]; then
  python_bin="$environment/bin/python"
else
  python_bin="${PYTHON_BIN:-python3}"
fi

UV_PROJECT_ENVIRONMENT="$environment" "$uv_bin" sync \
  --locked \
  --no-managed-python \
  --python "$python_bin" \
  "${extras[@]}"
