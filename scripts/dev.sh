#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_PORT="${CONCEPT_BRANCH_BACKEND_PORT:-8421}"
FRONTEND_PORT="${CONCEPT_BRANCH_FRONTEND_PORT:-5173}"
BACKEND_URL="http://127.0.0.1:${BACKEND_PORT}"
FRONTEND_URL="http://127.0.0.1:${FRONTEND_PORT}"
PIDS=()
kill_tree() { local pid="$1" child; for child in $(pgrep -P "$pid" 2>/dev/null || true); do kill_tree "$child"; done; kill "$pid" 2>/dev/null || true; }
cleanup() { for pid in "${PIDS[@]:-}"; do kill_tree "$pid"; done; }
trap cleanup EXIT INT TERM
setsid env CONCEPT_BRANCH_CORS_ORIGINS="$FRONTEND_URL" PYTHONPATH="$ROOT/backend" \
  "$ROOT/.venv/bin/uvicorn" concept_branch.app:app --host 127.0.0.1 --port "$BACKEND_PORT" --reload --no-access-log & PIDS+=("$!")
setsid env CONCEPT_BRANCH_BACKEND_URL="$BACKEND_URL" \
  sh -c "cd '$ROOT/frontend' && exec node node_modules/vite/bin/vite.js --host 127.0.0.1 --port '$FRONTEND_PORT' --strictPort" & PIDS+=("$!")
wait
