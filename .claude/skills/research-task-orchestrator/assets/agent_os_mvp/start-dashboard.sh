#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
LOG_DIR="$ROOT_DIR/logs"
BACKEND_PORT="${AGENT_OS_BACKEND_PORT:-8010}"
FRONTEND_PORT="${AGENT_OS_FRONTEND_PORT:-5174}"
BACKEND_HOST="${AGENT_OS_BACKEND_HOST:-127.0.0.1}"
FRONTEND_HOST="${AGENT_OS_FRONTEND_HOST:-127.0.0.1}"

mkdir -p "$LOG_DIR"
"$ROOT_DIR/stop-dashboard.sh" >/dev/null 2>&1 || true

cd "$BACKEND_DIR"
if [ ! -x ".venv/bin/python" ]; then
  echo "backend virtualenv not found: $BACKEND_DIR/.venv"
  exit 1
fi
nohup .venv/bin/python -m uvicorn app.main:app --host "$BACKEND_HOST" --port "$BACKEND_PORT" \
  >"$LOG_DIR/backend-${BACKEND_PORT}.out.log" 2>"$LOG_DIR/backend-${BACKEND_PORT}.err.log" &
echo $! > "$LOG_DIR/backend.pid"

cd "$FRONTEND_DIR"
VITE_API_BASE_URL="http://127.0.0.1:${BACKEND_PORT}" npm run build \
  >"$LOG_DIR/frontend-build-${FRONTEND_PORT}.out.log" 2>"$LOG_DIR/frontend-build-${FRONTEND_PORT}.err.log"
nohup npm run preview -- --host "$FRONTEND_HOST" --port "$FRONTEND_PORT" --strictPort \
  >"$LOG_DIR/frontend-${FRONTEND_PORT}.out.log" 2>"$LOG_DIR/frontend-${FRONTEND_PORT}.err.log" &
echo $! > "$LOG_DIR/frontend.pid"

echo "Backend:  http://127.0.0.1:${BACKEND_PORT}"
echo "Frontend: http://127.0.0.1:${FRONTEND_PORT}"
echo "Logs:     $LOG_DIR"
