#!/usr/bin/env bash
set -euo pipefail

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

if [[ -x "${DASHBOARD_DIR}/stop-dashboard.sh" ]]; then
  "${DASHBOARD_DIR}/stop-dashboard.sh"
else
  echo "Dashboard stop script not found: ${DASHBOARD_DIR}/stop-dashboard.sh"
fi
