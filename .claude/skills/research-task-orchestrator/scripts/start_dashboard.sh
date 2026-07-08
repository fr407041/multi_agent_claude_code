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

PROJECT_ROOT="$(find_project_root)"
DASHBOARD_DIR="${AI_COMPANY_DASHBOARD_DIR:-${PROJECT_ROOT}/agent_os_mvp}"
BACKEND_PORT="${AGENT_OS_BACKEND_PORT:-8010}"
FRONTEND_PORT="${AGENT_OS_FRONTEND_PORT:-5174}"

if [[ ! -x "${DASHBOARD_DIR}/backend/.venv/bin/python" || ! -d "${DASHBOARD_DIR}/frontend/node_modules" ]]; then
  bash "${SKILL_DIR}/scripts/install_dashboard.sh"
fi

cd "${DASHBOARD_DIR}"
chmod +x ./start-dashboard.sh ./stop-dashboard.sh ./smoke-dashboard.sh 2>/dev/null || true
AGENT_OS_BACKEND_PORT="${BACKEND_PORT}" AGENT_OS_FRONTEND_PORT="${FRONTEND_PORT}" ./start-dashboard.sh

echo "Dashboard URL: http://127.0.0.1:${FRONTEND_PORT}"
echo "Backend health: http://127.0.0.1:${BACKEND_PORT}/health"
echo "Artifacts root: ${PROJECT_ROOT}/results/ai_company_task_harness"
