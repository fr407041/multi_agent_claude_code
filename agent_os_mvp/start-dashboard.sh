#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="${ROOT_DIR}/backend"
FRONTEND_DIR="${ROOT_DIR}/frontend"
LOG_DIR="${ROOT_DIR}/logs"
mkdir -p "${LOG_DIR}"

"${ROOT_DIR}/stop-dashboard.sh" >/dev/null 2>&1 || true

if [[ ! -x "${BACKEND_DIR}/.venv/bin/python" ]]; then
  echo "Missing backend venv. Run: bash .claude/skills/research-task-orchestrator/scripts/install_dashboard.sh" >&2
  exit 1
fi
if [[ ! -d "${FRONTEND_DIR}/node_modules" ]]; then
  echo "Missing frontend node_modules. Run: bash .claude/skills/research-task-orchestrator/scripts/install_dashboard.sh" >&2
  exit 1
fi

cd "${BACKEND_DIR}"
nohup .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8010 >"${LOG_DIR}/backend.out.log" 2>"${LOG_DIR}/backend.err.log" &
echo $! > "${LOG_DIR}/backend.pid"

cd "${FRONTEND_DIR}"
nohup env VITE_API_BASE_URL="http://127.0.0.1:8010" npm run dev -- --host 127.0.0.1 --port 5174 >"${LOG_DIR}/frontend.out.log" 2>"${LOG_DIR}/frontend.err.log" &
echo $! > "${LOG_DIR}/frontend.pid"

echo "Backend:  http://127.0.0.1:8010"
echo "Frontend: http://127.0.0.1:5174"
echo "Logs:     ${LOG_DIR}"
