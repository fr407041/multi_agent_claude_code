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

if [[ ! -x "${DASHBOARD_DIR}/backend/.venv/bin/python" || ! -d "${DASHBOARD_DIR}/frontend/node_modules" ]]; then
  bash "${SKILL_DIR}/scripts/install_dashboard.sh"
fi

cd "${DASHBOARD_DIR}"
chmod +x ./start-dashboard.sh ./stop-dashboard.sh
./start-dashboard.sh

echo "Dashboard URL: http://127.0.0.1:5174"
echo "Backend health: http://127.0.0.1:8010/health"
echo "Artifacts root: ${PROJECT_ROOT}/results/ai_company_task_harness"
