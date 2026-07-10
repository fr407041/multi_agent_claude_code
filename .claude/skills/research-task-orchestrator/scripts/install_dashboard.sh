#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ASSET_DIR="${SKILL_DIR}/assets/agent_os_mvp"

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

require_command() {
  local name="${1:?command name required}"
  if ! command -v "${name}" >/dev/null 2>&1; then
    echo "Required command not found: ${name}" >&2
    exit 1
  fi
}

copy_dashboard_assets() {
  local dest="${1:?destination required}"
  mkdir -p "${dest}"
  tar -C "${ASSET_DIR}" \
    --exclude='./frontend/node_modules' \
    --exclude='./frontend/dist' \
    --exclude='./backend/.venv' \
    --exclude='./logs' \
    --exclude='./data' \
    --exclude='./*.db' \
    --exclude='./*.sqlite' \
    -cf - . | tar -C "${dest}" -xf -

  find "${dest}" \( \
    -name node_modules -o \
    -name .venv -o \
    -name logs -o \
    -name data -o \
    -name dist -o \
    -name __pycache__ \
  \) -type d -prune -exec rm -rf {} +
  find "${dest}" -name '*.pyc' -delete
  chmod +x "${dest}/start-dashboard.sh" "${dest}/stop-dashboard.sh" "${dest}/smoke-dashboard.sh" 2>/dev/null || true
}

if [[ ! -d "${ASSET_DIR}" ]]; then
  echo "Dashboard assets not found: ${ASSET_DIR}" >&2
  exit 1
fi

require_command python3
require_command node
require_command npm

PROJECT_ROOT="$(find_project_root)"
DASHBOARD_DIR="${AI_COMPANY_DASHBOARD_DIR:-${PROJECT_ROOT}/agent_os_mvp}"

copy_dashboard_assets "${DASHBOARD_DIR}"

cd "${DASHBOARD_DIR}/backend"
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

cd "${DASHBOARD_DIR}/frontend"
npm install

cat <<EOF
Dashboard installed.
Dashboard dir: ${DASHBOARD_DIR}
Backend health: http://127.0.0.1:8010/health
Frontend URL: http://127.0.0.1:5174
Start with:
  bash ${SKILL_DIR}/scripts/start_dashboard.sh
EOF
