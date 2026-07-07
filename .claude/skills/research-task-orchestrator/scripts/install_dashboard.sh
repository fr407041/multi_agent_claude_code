#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
DASHBOARD="$ROOT/agent_os_mvp"

echo "AI-company dashboard install check"
if [ ! -d "$DASHBOARD" ]; then
  echo "Missing dashboard source: $DASHBOARD" >&2
  exit 1
fi

if command -v python3 >/dev/null 2>&1; then
  echo "python3: $(python3 --version)"
else
  echo "python3 is required for backend setup" >&2
  exit 1
fi

if command -v npm >/dev/null 2>&1; then
  echo "npm: $(npm --version)"
else
  echo "npm is required for frontend setup" >&2
  exit 1
fi

echo "Dashboard source is present. Install backend/frontend dependencies in agent_os_mvp when running the full dashboard."
echo "For the issue #1 smoke test, dashboard install is not required."
