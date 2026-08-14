#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
command -v rg >/dev/null || {
  echo "ripgrep (rg) is required by scripts/verify.sh" >&2
  exit 1
}
uv run pytest
npm --prefix frontend run build
bash scripts/run_e2e.sh
if rg -n --hidden --glob '!node_modules/**' --glob '!.git/**' --glob '!uv.lock' '(sk-|e2e-test-key|super-secret-value|new-secret|test-key)' backend frontend/src README.md 2>/dev/null; then
  echo "Potential secret-like value found in production files" >&2
  exit 1
fi
echo "Concept Branch verification passed"
