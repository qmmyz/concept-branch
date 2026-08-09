#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
PIDS=()
read -r BACKEND_PORT FRONTEND_PORT PROVIDER_PORT < <(
  "$ROOT/.venv/bin/python" - <<'PY'
import socket
ports = []
sockets = []
for _ in range(3):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sockets.append(sock)
    ports.append(sock.getsockname()[1])
print(*ports)
for sock in sockets:
    sock.close()
PY
)
BACKEND_PORT="${CONCEPT_BRANCH_E2E_BACKEND_PORT:-$BACKEND_PORT}"
FRONTEND_PORT="${CONCEPT_BRANCH_E2E_FRONTEND_PORT:-$FRONTEND_PORT}"
PROVIDER_PORT="${CONCEPT_BRANCH_E2E_PROVIDER_PORT:-$PROVIDER_PORT}"
BACKEND_URL="http://127.0.0.1:${BACKEND_PORT}"
FRONTEND_URL="http://127.0.0.1:${FRONTEND_PORT}"
PROVIDER_URL="http://127.0.0.1:${PROVIDER_PORT}"
kill_tree() {
  local pid="$1" child
  for child in $(pgrep -P "$pid" 2>/dev/null || true); do kill_tree "$child"; done
  kill "$pid" 2>/dev/null || true
}
cleanup() {
  for pid in "${PIDS[@]:-}"; do kill_tree "$pid"; done
  wait 2>/dev/null || true
  rm -rf "$TMP"
}
trap cleanup EXIT

setsid env CONCEPT_BRANCH_MOCK_PROVIDER_PORT="$PROVIDER_PORT" \
  "$ROOT/.venv/bin/python" "$ROOT/scripts/mock_provider.py" & PIDS+=("$!")
setsid env CONCEPT_BRANCH_DB="$TMP/e2e.sqlite3" CONCEPT_BRANCH_CONFIG_DIR="$TMP/config" \
  CONCEPT_BRANCH_SERVE_FRONTEND=0 CONCEPT_BRANCH_CORS_ORIGINS="$FRONTEND_URL" PYTHONPATH="$ROOT/backend" \
  "$ROOT/.venv/bin/uvicorn" concept_branch.app:app --host 127.0.0.1 --port "$BACKEND_PORT" --log-level warning & PIDS+=("$!")
setsid env CONCEPT_BRANCH_BACKEND_URL="$BACKEND_URL" \
  sh -c "cd '$ROOT/frontend' && exec node node_modules/vite/bin/vite.js --host 127.0.0.1 --port '$FRONTEND_PORT' --strictPort" & PIDS+=("$!")

wait_for_url() {
  local url="$1"
  for _ in {1..60}; do
    if curl -fsS "$url" >/dev/null 2>&1; then return 0; fi
    sleep 0.2
  done
  echo "Timed out waiting for $url" >&2
  return 1
}

provider_ready=0
for _ in {1..60}; do
  if curl -fsS -X POST -H 'Authorization: Bearer e2e-test-key' -H 'Content-Type: application/json' \
    -d '{"model":"mock","messages":[]}' "$PROVIDER_URL/v1/chat/completions" >/dev/null 2>&1; then
    provider_ready=1
    break
  fi
  sleep 0.2
done
if [ "$provider_ready" -ne 1 ]; then
  echo "Timed out waiting for mock provider at $PROVIDER_URL" >&2
  exit 1
fi
wait_for_url "$BACKEND_URL/api/health"
wait_for_url "$FRONTEND_URL"

CONCEPT_BRANCH_E2E_BASE_URL="$FRONTEND_URL" \
CONCEPT_BRANCH_E2E_PROVIDER_URL="$PROVIDER_URL/v1" \
  npm --prefix "$ROOT/frontend" run test:e2e
