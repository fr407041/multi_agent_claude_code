#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="${ROOT_DIR}/backend"
FRONTEND_DIR="${ROOT_DIR}/frontend"
BACKEND_PORT="${AGENT_OS_BACKEND_PORT:-8010}"
FRONTEND_PORT="${AGENT_OS_FRONTEND_PORT:-5174}"
BACKEND_URL="http://127.0.0.1:${BACKEND_PORT}"
FRONTEND_URL="http://127.0.0.1:${FRONTEND_PORT}"

fail() {
  echo "dashboard smoke failed: $*" >&2
  exit 1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "missing command '$1'"
}

port_open() {
  python3 - "$1" <<'PY'
import socket, sys
port = int(sys.argv[1])
sock = socket.socket()
sock.settimeout(0.3)
try:
    sock.connect(("127.0.0.1", port))
except OSError:
    raise SystemExit(1)
finally:
    sock.close()
raise SystemExit(0)
PY
}

health_app_root() {
  python3 - "$BACKEND_URL/health" <<'PY'
import json, sys, urllib.request
try:
    with urllib.request.urlopen(sys.argv[1], timeout=3) as response:
        payload = json.loads(response.read().decode("utf-8"))
except Exception:
    raise SystemExit(1)
print(payload.get("app_root", ""))
PY
}

frontend_title_ok() {
  python3 - "$FRONTEND_URL" <<'PY'
import sys, urllib.request
try:
    with urllib.request.urlopen(sys.argv[1], timeout=3) as response:
        html = response.read().decode("utf-8", errors="replace")
except Exception:
    raise SystemExit(1)
raise SystemExit(0 if "<title>AI Company Dashboard</title>" in html else 1)
PY
}

require_cmd python3
require_cmd curl

test -x "${BACKEND_DIR}/.venv/bin/python" || fail "missing backend venv. Run: bash .claude/skills/research-task-orchestrator/scripts/install_dashboard.sh"
test -d "${FRONTEND_DIR}/node_modules" || fail "missing frontend node_modules. Run: bash .claude/skills/research-task-orchestrator/scripts/install_dashboard.sh"

if port_open "$BACKEND_PORT"; then
  actual_root="$(health_app_root || true)"
  if [[ "$actual_root" != "$ROOT_DIR" ]]; then
    fail "backend port ${BACKEND_PORT} is occupied by another service. Set AGENT_OS_BACKEND_PORT to another port or stop the stale service."
  fi
else
  if port_open "$FRONTEND_PORT"; then
    fail "frontend port ${FRONTEND_PORT} is occupied before backend starts. Set AGENT_OS_FRONTEND_PORT to another port or stop the stale service."
  fi
  AGENT_OS_BACKEND_PORT="$BACKEND_PORT" AGENT_OS_FRONTEND_PORT="$FRONTEND_PORT" bash "${ROOT_DIR}/start-dashboard.sh" >/dev/null
  sleep 3
fi

actual_root="$(health_app_root || true)"
test "$actual_root" = "$ROOT_DIR" || fail "backend health marker mismatch. expected=${ROOT_DIR} actual=${actual_root:-unavailable}"

curl -fsS "${BACKEND_URL}/api/ai-company-monitor" >/dev/null || fail "backend monitor API unavailable: ${BACKEND_URL}/api/ai-company-monitor"
frontend_title_ok || fail "frontend title check failed at ${FRONTEND_URL}; this may be a stale Vite service from another checkout"

echo "Dashboard smoke check passed."
echo "Backend:  ${BACKEND_URL}"
echo "Frontend: ${FRONTEND_URL}"
echo "App root: ${ROOT_DIR}"
