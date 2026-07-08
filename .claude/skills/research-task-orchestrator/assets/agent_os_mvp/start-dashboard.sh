#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="${ROOT_DIR}/backend"
FRONTEND_DIR="${ROOT_DIR}/frontend"
LOG_DIR="${ROOT_DIR}/logs"
BACKEND_PORT="${AGENT_OS_BACKEND_PORT:-8010}"
FRONTEND_PORT="${AGENT_OS_FRONTEND_PORT:-5174}"
mkdir -p "${LOG_DIR}"
"${ROOT_DIR}/stop-dashboard.sh" >/dev/null 2>&1 || true

if [[ ! -x "${BACKEND_DIR}/.venv/bin/python" ]]; then
  echo "Missing backend venv. Run install_dashboard.sh first." >&2
  exit 1
fi
if [[ ! -d "${FRONTEND_DIR}/node_modules" ]]; then
  echo "Missing frontend node_modules. Run install_dashboard.sh first." >&2
  exit 1
fi

cd "${BACKEND_DIR}"
nohup .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "${BACKEND_PORT}" >"${LOG_DIR}/backend-${BACKEND_PORT}.out.log" 2>"${LOG_DIR}/backend-${BACKEND_PORT}.err.log" &
echo $! > "${LOG_DIR}/backend.pid"
cd "${FRONTEND_DIR}"
nohup env VITE_API_BASE_URL="http://127.0.0.1:${BACKEND_PORT}" npm run dev -- --host 127.0.0.1 --port "${FRONTEND_PORT}" >"${LOG_DIR}/frontend-${FRONTEND_PORT}.out.log" 2>"${LOG_DIR}/frontend-${FRONTEND_PORT}.err.log" &
echo $! > "${LOG_DIR}/frontend.pid"
echo "Backend:  http://127.0.0.1:${BACKEND_PORT}"
echo "Frontend: http://127.0.0.1:${FRONTEND_PORT}"
