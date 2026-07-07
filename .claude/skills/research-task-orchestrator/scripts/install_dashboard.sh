#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
DASHBOARD="$ROOT/agent_os_mvp"

echo "AI-company dashboard placeholder check"
if [ ! -d "$DASHBOARD" ]; then
  echo "Missing dashboard placeholder: $DASHBOARD" >&2
  exit 1
fi

if command -v python3 >/dev/null 2>&1; then
  echo "python3: $(python3 --version)"
else
  echo "python3 was not found. The mock harness may still work with python if available." >&2
fi

if command -v npm >/dev/null 2>&1; then
  echo "npm: $(npm --version)"
else
  echo "npm was not found. This is acceptable for the mock-first smoke package." >&2
fi

echo "Dashboard placeholder is present. No backend/frontend dependencies were installed."
echo "A runnable dashboard is a separate follow-up task."
