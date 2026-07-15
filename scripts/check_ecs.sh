#!/usr/bin/env bash
set -euo pipefail
BASE_URL="${1:-http://127.0.0.1:8000}"
printf 'Health:\n'
curl -fsS "$BASE_URL/health"; printf '\n'
for path in / /login /manage /upload; do
  printf '%s HTTP status: ' "$path"
  curl -sS -o /dev/null -w '%{http_code}\n' "$BASE_URL$path"
done
