#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
DASHBOARD="$ROOT/agent_os_mvp"

if [ ! -d "$DASHBOARD" ]; then
  echo "Missing dashboard source: $DASHBOARD" >&2
  exit 1
fi

echo "Dashboard source is available at: $DASHBOARD"
echo "Full dashboard runtime requires backend/frontend dependency installation."
echo "Expected URL after starting the full dashboard: http://127.0.0.1:5174"
