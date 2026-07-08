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

test -d "${SKILL_DIR}/assets/agent_os_mvp"
test -x "${DASHBOARD_DIR}/smoke-dashboard.sh" || {
  echo "Missing dashboard smoke script. Run: bash .claude/skills/research-task-orchestrator/scripts/install_dashboard.sh" >&2
  exit 1
}

bash "${DASHBOARD_DIR}/smoke-dashboard.sh"
