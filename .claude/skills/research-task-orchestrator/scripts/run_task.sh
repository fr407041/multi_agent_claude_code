#!/usr/bin/env bash
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_ROOT="${AI_COMPANY_PROJECT_ROOT:-$(pwd)}"
RUNTIME="$PROJECT_ROOT/.ai-company/runtime/current"

if [[ ! -x "$RUNTIME/scripts/run_ai_company_task_harness.py" && ! -f "$RUNTIME/scripts/run_ai_company_task_harness.py" ]]; then
  AI_COMPANY_PROJECT_ROOT="$PROJECT_ROOT" bash "$SKILL_DIR/scripts/install_runtime.sh"
fi

if [[ $# -lt 1 ]]; then
  SPEC="$RUNTIME/docs/ai_specs/ai-company-release-readiness-strict-demo.json"
  shift 0
else
  SPEC="$1"
  shift
  if [[ "$SPEC" != /* ]]; then
    if [[ -f "$PROJECT_ROOT/$SPEC" ]]; then
      SPEC="$PROJECT_ROOT/$SPEC"
    elif [[ -f "$RUNTIME/$SPEC" ]]; then
      SPEC="$RUNTIME/$SPEC"
    fi
  fi
fi

export AI_COMPANY_PACKAGE_PROFILE=runtime
export AI_COMPANY_WORKSPACE_ROOT="$PROJECT_ROOT"
export AI_COMPANY_SKILL_ROOT="$SKILL_DIR"
exec python3 "$RUNTIME/scripts/run_ai_company_task_harness.py" \
  "$SPEC" --out-root "$PROJECT_ROOT/results/ai_company_task_harness" "$@"
