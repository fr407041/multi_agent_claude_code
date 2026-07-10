#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

find_project_root() {
  if [[ -n "${AI_COMPANY_PROJECT_ROOT:-}" ]]; then
    cd "${AI_COMPANY_PROJECT_ROOT}" && pwd
    return
  fi
  if git rev-parse --show-toplevel >/dev/null 2>&1; then
    git rev-parse --show-toplevel
    return
  fi
  pwd
}

port_busy() {
  local port="${1:?port required}"
  if command -v ss >/dev/null 2>&1; then
    ss -ltn | awk '{print $4}' | grep -Eq "[:.]${port}$"
    return
  fi
  if command -v lsof >/dev/null 2>&1; then
    lsof -iTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1
    return
  fi
  return 1
}

PROJECT_ROOT="$(find_project_root)"
DASHBOARD_DIR="${AI_COMPANY_DASHBOARD_DIR:-${PROJECT_ROOT}/agent_os_mvp}"
BACKEND_PORT="${AGENT_OS_BACKEND_PORT:-8010}"
FRONTEND_PORT="${AGENT_OS_FRONTEND_PORT:-5174}"
BACKEND_HOST="${AGENT_OS_BACKEND_HOST:-127.0.0.1}"
FRONTEND_HOST="${AGENT_OS_FRONTEND_HOST:-127.0.0.1}"

if [[ ! -x "${DASHBOARD_DIR}/backend/.venv/bin/python" || ! -d "${DASHBOARD_DIR}/frontend/node_modules" ]]; then
  bash "${SKILL_DIR}/scripts/install_dashboard.sh"
fi

if port_busy "${BACKEND_PORT}"; then
  echo "Backend port is already in use: ${BACKEND_PORT}" >&2
  exit 1
fi
if port_busy "${FRONTEND_PORT}"; then
  echo "Frontend port is already in use: ${FRONTEND_PORT}" >&2
  exit 1
fi

cd "${DASHBOARD_DIR}"
chmod +x ./start-dashboard.sh ./stop-dashboard.sh ./smoke-dashboard.sh 2>/dev/null || true
AGENT_OS_BACKEND_PORT="${BACKEND_PORT}" \
AGENT_OS_FRONTEND_PORT="${FRONTEND_PORT}" \
AGENT_OS_BACKEND_HOST="${BACKEND_HOST}" \
AGENT_OS_FRONTEND_HOST="${FRONTEND_HOST}" \
./start-dashboard.sh

cat <<EOF
Dashboard is starting.
Backend health: http://127.0.0.1:${BACKEND_PORT}/health
Frontend URL: http://127.0.0.1:${FRONTEND_PORT}
Artifacts root: ${PROJECT_ROOT}/results/ai_company_task_harness
EOF
