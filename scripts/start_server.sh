#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="${CONCEPT_BRANCH_HOST:-127.0.0.1}"
PORT="${CONCEPT_BRANCH_PORT:-8421}"
cd "$ROOT"

if [ ! -d frontend/dist ]; then
  echo "Frontend is not built. Run: npm --prefix frontend run build" >&2
  exit 1
fi
if [ ! -x .venv/bin/uvicorn ]; then
  echo "Dependencies are not installed. Run: uv sync && npm --prefix frontend ci" >&2
  exit 1
fi
if [ "$HOST" != "127.0.0.1" ] && [ "$HOST" != "localhost" ]; then
  echo "Warning: listening on $HOST. Use a trusted network or an HTTPS reverse proxy." >&2
fi

exec env PYTHONPATH=backend .venv/bin/uvicorn concept_branch.app:app \
  --host "$HOST" --port "$PORT" --log-level info --no-access-log
