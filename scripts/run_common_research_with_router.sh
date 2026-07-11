#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SPEC_PATH="${1:-${ROOT_DIR}/docs/ai_specs/ai-company-release-readiness-strict-demo.json}"
MODE="${2:-live}"
MODEL_MODELS_URL="${MODEL_MODELS_URL:-${OPENAI_BASE_URL:-http://127.0.0.1:11434/v1}/models}"
MODEL_TAGS_URL="${MODEL_TAGS_URL:-http://127.0.0.1:11434/api/tags}"
MODEL_CHAT_URL="${MODEL_CHAT_URL:-}"
MODEL_NATIVE_GENERATE_URL="${MODEL_NATIVE_GENERATE_URL:-}"
PROVIDER_DIAGNOSTIC_OUT="${PROVIDER_DIAGNOSTIC_OUT:-${ROOT_DIR}/results/provider_diagnostic_report.json}"
CCR_BASE_URL="${CCR_BASE_URL:-http://127.0.0.1:3456}"
CCR_HEALTH_URL="${CCR_HEALTH_URL:-http://127.0.0.1:3456/health}"
CCR_MESSAGES_URL="${CCR_MESSAGES_URL:-${CCR_BASE_URL%/}/v1/messages}"
LIVE_TASK_URL="${AI_COMPANY_LIVE_TASK_URL:-}"

export AI_COMPANY_WORKER_SCRIPTS_DIR="${ROOT_DIR}/scripts"
if [[ -z "${LIVE_TASK_URL}" && -z "${CLAUDE_MODEL_ALIAS:-}" && -z "${CCR_PREFERRED_MODEL:-}" ]]; then
  echo "Direct CCR /v1/messages live mode requires CLAUDE_MODEL_ALIAS or CCR_PREFERRED_MODEL." >&2
  echo "If your router exposes a provider-neutral task API, set AI_COMPANY_LIVE_TASK_URL instead." >&2
  exit 2
fi
export CLAUDE_MODEL_ALIAS="${CLAUDE_MODEL_ALIAS:-${CCR_PREFERRED_MODEL:-}}"
export CLAUDE_TOOLS_VALUE="${CLAUDE_TOOLS_VALUE-}"
export CCR_PREFERRED_MODEL="${CCR_PREFERRED_MODEL:-${CLAUDE_MODEL_ALIAS}}"
export CCR_MAX_OUTPUT_TOKENS="${CCR_MAX_OUTPUT_TOKENS:-1024}"
export CCR_API_KEY="${CCR_API_KEY:-local-router-token}"
export CCR_MESSAGES_URL

require_command() {
  local name="${1:?command name required}"
  if ! command -v "${name}" >/dev/null 2>&1; then
    echo "Required command not found: ${name}" >&2
    exit 1
  fi
}

probe_url() {
  local url="${1:?url required}"
  curl -fsS --max-time "${LIVE_PREFLIGHT_TIMEOUT_SEC:-15}" "${url}" >/dev/null
}

probe_ccr_messages() {
  python3 - "$CCR_MESSAGES_URL" "$CLAUDE_MODEL_ALIAS" "${CCR_MAX_OUTPUT_TOKENS}" <<'PY'
import json
import sys
import urllib.error
import urllib.request

url, model, max_tokens = sys.argv[1], sys.argv[2], int(sys.argv[3])
payload = {
    "model": model,
    "max_tokens": min(max_tokens, 64),
    "messages": [{"role": "user", "content": "Reply with exactly: ok"}],
}
req = urllib.request.Request(
    url,
    data=json.dumps(payload).encode("utf-8"),
    headers={"Content-Type": "application/json", "x-api-key": __import__("os").environ.get("CCR_API_KEY", "local-router-token"), "anthropic-version": "2023-06-01"},
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8", errors="ignore")
except urllib.error.HTTPError as exc:
    sys.stderr.write(f"CCR /v1/messages failed: HTTP {exc.code}\n")
    sys.stderr.write(exc.read().decode("utf-8", errors="ignore")[:800] + "\n")
    raise SystemExit(1)
except Exception as exc:
    sys.stderr.write(f"CCR /v1/messages failed: {exc}\n")
    raise SystemExit(1)
if not body.strip():
    sys.stderr.write("CCR /v1/messages returned an empty body\n")
    raise SystemExit(1)
try:
    parsed = json.loads(body)
except json.JSONDecodeError:
    sys.stderr.write("CCR /v1/messages returned non-JSON output\n")
    sys.stderr.write(body[:800] + "\n")
    raise SystemExit(1)
if "error" in parsed:
    sys.stderr.write("CCR /v1/messages returned an error payload\n")
    sys.stderr.write(json.dumps(parsed["error"], ensure_ascii=False)[:800] + "\n")
    raise SystemExit(1)
print("CCR /v1/messages preflight passed")
PY
}

if [[ "${MODE}" != "live" ]]; then
  echo "This runner is intended for live mode. Use scripts/run_ai_company_task_harness.py --mode mock for mock smoke tests." >&2
  exit 2
fi

require_command bash
require_command curl
require_command python3

run_provider_diagnostic() {
  local require_direct="${AI_COMPANY_REQUIRE_DIRECT_PROVIDER_COMPLETION:-0}"
  local -a diagnostic_args
  diagnostic_args=(
    "${ROOT_DIR}/scripts/probe_provider_diagnostic.py"
    --tags-url "${MODEL_TAGS_URL}"
    --models-url "${MODEL_MODELS_URL}"
    --model "${PROVIDER_DIAGNOSTIC_MODEL:-${CLAUDE_MODEL_ALIAS:-${CCR_PREFERRED_MODEL:-}}}"
    --timeout-sec "${LIVE_PREFLIGHT_TIMEOUT_SEC:-15}"
    --output "${PROVIDER_DIAGNOSTIC_OUT}"
  )
  if [[ -n "${MODEL_CHAT_URL}" ]]; then
    diagnostic_args+=(--chat-url "${MODEL_CHAT_URL}")
  fi
  if [[ -n "${MODEL_NATIVE_GENERATE_URL}" ]]; then
    diagnostic_args+=(--native-generate-url "${MODEL_NATIVE_GENERATE_URL}")
  fi
  if [[ "${require_direct}" == "1" ]]; then
    diagnostic_args+=(--require-direct-completion)
  fi

  if ! python3 "${diagnostic_args[@]}"; then
    if [[ "${require_direct}" == "1" ]]; then
      echo "Required direct provider completion diagnostic failed. See ${PROVIDER_DIAGNOSTIC_OUT}" >&2
      exit 1
    fi
    echo "Provider diagnostic warning recorded at ${PROVIDER_DIAGNOSTIC_OUT}; continuing to router live gates." >&2
  else
    echo "Provider diagnostic recorded at ${PROVIDER_DIAGNOSTIC_OUT}"
  fi
}

if [[ "${AI_COMPANY_PROBE_MODEL_ENDPOINTS:-0}" == "1" ]]; then
  run_provider_diagnostic
fi

if ! probe_url "${CCR_HEALTH_URL}"; then
  echo "Claude Code Router /health failed: ${CCR_HEALTH_URL}" >&2
  exit 1
fi

if [[ -z "${LIVE_TASK_URL}" ]]; then
  probe_ccr_messages
else
  echo "Using provider-neutral AI_COMPANY_LIVE_TASK_URL=${LIVE_TASK_URL}"
fi

if [[ "${LIVE_SIDE_EFFECT_SMOKE:-1}" != "0" ]]; then
  python3 "${ROOT_DIR}/scripts/smoke_live_tool_side_effect.py"
fi

cd "${ROOT_DIR}"
python3 "${ROOT_DIR}/scripts/run_ai_company_live_router.py" "${SPEC_PATH}"
