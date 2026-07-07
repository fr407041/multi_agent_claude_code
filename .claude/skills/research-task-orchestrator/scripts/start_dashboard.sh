#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
DASHBOARD="$ROOT/agent_os_mvp"

if [ ! -d "$DASHBOARD" ]; then
  echo "Missing dashboard placeholder: $DASHBOARD" >&2
  exit 1
fi

echo "Dashboard placeholder is available at: $DASHBOARD"
echo "This public smoke package does not start a web dashboard yet."
echo "No service was launched, and http://127.0.0.1:5174 is not expected to respond."
echo "Run the mock demo first, then inspect JSON artifacts under results/.../<run-id>/ai_company/."
