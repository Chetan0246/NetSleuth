#!/usr/bin/env bash
# Start the NetSleuth backend and frontend development servers.
#
# Usage:  ./scripts/run_dev.sh
# Stop with Ctrl-C (both servers are terminated together).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_PORT="${NETSLEUTH_PORT:-8000}"
FRONTEND_PORT="${NETSLEUTH_FRONTEND_PORT:-5173}"

PYTHON="${PYTHON:-python3}"

if [[ ! -d "$ROOT/frontend/node_modules" ]]; then
  echo "frontend dependencies are missing; run: cd frontend && npm install" >&2
  exit 1
fi

"$PYTHON" - <<'PY' || { echo "backend dependencies are missing; run: pip install -r backend/requirements.txt" >&2; exit 1; }
import importlib
for module in ("fastapi", "uvicorn", "networkx", "pydantic", "httpx"):
    importlib.import_module(module)
PY

cleanup() {
  echo
  echo "stopping NetSleuth…"
  # Kill the whole process group of each background job.
  for pid in "${BACKEND_PID:-}" "${FRONTEND_PID:-}"; do
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
    fi
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "starting backend on http://127.0.0.1:${BACKEND_PORT} (API docs at /docs)"
(
  cd "$ROOT/backend"
  exec "$PYTHON" -m uvicorn app.main:app --host 127.0.0.1 --port "$BACKEND_PORT" --reload
) &
BACKEND_PID=$!

# Wait for the health endpoint so the frontend does not report a dead backend on
# its very first render.
for _ in $(seq 1 40); do
  if "$PYTHON" - "$BACKEND_PORT" <<'PY'
import json, sys, urllib.request
port = sys.argv[1]
try:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/v1/health", timeout=1) as response:
        sys.exit(0 if json.load(response).get("status") == "healthy" else 1)
except Exception:
    sys.exit(1)
PY
  then
    echo "backend is healthy"
    break
  fi
  sleep 0.25
done

echo "starting frontend on http://127.0.0.1:${FRONTEND_PORT}"
(
  cd "$ROOT/frontend"
  exec npm run dev -- --port "$FRONTEND_PORT"
) &
FRONTEND_PID=$!

echo
echo "NetSleuth is running:"
echo "  frontend  http://127.0.0.1:${FRONTEND_PORT}"
echo "  API       http://127.0.0.1:${BACKEND_PORT}/api/v1/health"
echo "  API docs  http://127.0.0.1:${BACKEND_PORT}/docs"
echo
echo "Press Ctrl-C to stop."
wait
