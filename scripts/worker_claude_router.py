from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def file_state(scope: Path, files: list[str]) -> dict[str, dict[str, Any]]:
    state: dict[str, Any] = {}
    for rel in files:
        path = scope / rel
        entry: dict[str, Any] = {"exists": path.exists()}
        if path.exists() and path.is_file():
            entry["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        state[rel] = entry
    return state


def changed_files(before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]]) -> list[str]:
    return [path for path, prev in before.items() if prev != after.get(path, {})]


def call_ccr(prompt: str, timeout: int) -> tuple[int, str]:
    url = os.environ.get("CCR_MESSAGES_URL", "http://127.0.0.1:3456/v1/messages")
    model = os.environ.get("CLAUDE_MODEL_ALIAS", "ollama,qwen2.5-coder:3b")
    api_key = os.environ.get("CCR_API_KEY", os.environ.get("ANTHROPIC_API_KEY", "local-router-token"))
    max_tokens = int(os.environ.get("CCR_MAX_OUTPUT_TOKENS", "1024"))
    payload = {"model": model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]}
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-api-key": api_key, "anthropic-version": "2023-06-01"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        return 1, f"STATUS: ROUTER_ERROR\nFILES:\nTESTS: not-run\nSUMMARY: HTTP {exc.code} from CCR\n\n{detail[:1200]}\n"
    except TimeoutError:
        return 124, "STATUS: CHILD_TIMEOUT\nFILES:\nTESTS: not-run\nSUMMARY: CCR request timed out\n"
    except Exception as exc:
        return 1, f"STATUS: ROUTER_ERROR\nFILES:\nTESTS: not-run\nSUMMARY: {exc}\n"
    if not body.strip():
        return 1, "STATUS: ROUTER_ERROR\nFILES:\nTESTS: not-run\nSUMMARY: empty response from CCR\n"
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return 0, body
    if isinstance(parsed.get("content"), list):
        text = "\n".join(str(item.get("text", "")) for item in parsed["content"] if isinstance(item, dict))
        return 0, text or body
    if isinstance(parsed.get("choices"), list) and parsed["choices"]:
        message = parsed["choices"][0].get("message", {})
        return 0, str(message.get("content") or parsed["choices"][0].get("text") or body)
    return 0, body


def status_from_raw(raw: str, exit_code: int) -> str:
    lowered = raw.lower()
    match = re.search(r"(?im)^STATUS:\s*(SUCCESS|NEEDS_REPLAN|OVERFLOW_DETECTED|ROUTER_ERROR|CHILD_LIMIT_REACHED|CHILD_TIMEOUT|PSEUDO_TOOL_CALL_DETECTED|FAILED)\b", raw)
    if match:
        return match.group(1)
    if exit_code == 124:
        return "CHILD_TIMEOUT"
    overflow_error_patterns = [
        r"maximum context length (?:is|of|exceeded)",
        r"context length exceeded",
        r"context window exceeded",
        r"requested \d+ output tokens",
        r"output tokens exceeds?",
        r"too many tokens",
    ]
    if exit_code != 0 and any(re.search(pattern, lowered) for pattern in overflow_error_patterns):
        return "OVERFLOW_DETECTED"
    if exit_code != 0 or not raw.strip():
        return "ROUTER_ERROR"
    return "SUCCESS"


def looks_like_pseudo_tool_call(raw: str) -> bool:
    text = raw.strip()
    if not text:
        return False
    fenced = re.fullmatch(r"```(?:json)?\s*\n(.*?)\n```", text, flags=re.S | re.I)
    if fenced:
        text = fenced.group(1).strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        lowered = text.lower()
        return bool(
            re.search(r'"\s*(name|tool|tool_name)\s*"\s*:\s*"\s*(write|edit|create|bash|shell)', lowered)
            or "tool_calls" in lowered
            or ("file_path" in lowered and "arguments" in lowered and not re.search(r"(?im)^STATUS:\s*SUCCESS\b", text))
        )
    candidates = payload if isinstance(payload, list) else [payload]
    for item in candidates:
        if not isinstance(item, dict):
            continue
        if "tool_calls" in item:
            return True
        name = str(item.get("name") or item.get("tool") or item.get("tool_name") or "").lower()
        arguments = item.get("arguments")
        if name in {"write", "edit", "create", "bash", "shell"} and isinstance(arguments, dict):
            return True
        if "file_path" in item and ("content" in item or "arguments" in item):
            return True
    return False


def extract_summary_content(raw: str) -> str:
    text = raw.strip()
    fence = re.fullmatch(r"```[A-Za-z0-9_-]*\s*\n(.*?)\n```", text, flags=re.S)
    if fence:
        text = fence.group(1).strip()
    content_block = re.search(r"CONTENT_START\s*\n(.*?)\nCONTENT_END", text, flags=re.S)
    if content_block:
        return content_block.group(1).strip() + "\n"
    content_start = re.search(r"CONTENT_START\s*\n(.*)", text, flags=re.S)
    if content_start:
        return content_start.group(1).strip() + "\n"
    lines = [line for line in text.splitlines() if not re.match(r"^(STATUS|FILES|TESTS|SUMMARY):", line.strip(), flags=re.I)]
    cleaned = "\n".join(lines).strip()
    return (cleaned or text) + "\n"


def build_prompt(job: dict[str, Any], worker_kind: str, scope: Path) -> str:
    files = job.get("files", [])
    context_blocks = []
    context_files = [str(item) for item in job.get("template_context_files", [])]
    if worker_kind == "summary":
        for rel in ["evidence_summary.txt", "summary_context.txt"]:
            if rel not in context_files and (scope / rel).exists():
                context_files.append(rel)
    for rel in [*context_files, *files]:
        path = scope / rel
        if path.exists() and path.is_file():
            text = path.read_text(encoding="utf-8", errors="ignore")
            context_blocks.append(f"File: {rel}\n{text[:6000]}")
    requirements_path = scope / "artifact_requirements.json"
    verifier_contract = ""
    if worker_kind == "summary" and requirements_path.exists():
        requirements_text = requirements_path.read_text(encoding="utf-8", errors="ignore")
        context_blocks.append(f"Verifier contract: artifact_requirements.json\n{requirements_text[:3000]}")
        try:
            requirements = json.loads(requirements_text)
            exact_patterns = ", ".join(str(item) for item in requirements.get("required_patterns", []))
            exact_keywords = ", ".join(str(item) for item in requirements.get("required_keywords_all", []))
            verifier_contract = f"""
Verifier must pass:
- Maximum words: {requirements.get("max_words", "not specified")}
- Required keywords: {exact_keywords}
- Required exact/numeric patterns: {exact_patterns}
"""
        except json.JSONDecodeError:
            verifier_contract = ""
    context = "\n\n".join(context_blocks) or "No target file content exists yet."
    if worker_kind == "summary":
        return f"""You are a strict synthesis worker.

Task:
{job.get('instruction', '')}

Evidence and target context:
{context}
{verifier_contract}

Return exactly this format:
CONTENT_START
- <bullet 1>
- <bullet 2>
- <bullet 3>
Takeaway: <one short sentence>
CONTENT_END

Keep the content concise, evidence-grounded, and explicit about uncertainty.
Use exactly 3 bullets plus 1 Takeaway sentence.
Each bullet must be one sentence.
Stay under the verifier maximum word count.
If the context or verifier contract contains exact numeric phrases or regex-like patterns, preserve those exact phrases.
Do not add headings unless the task explicitly asks for headings.
Do not use nested bullets.
Do not claim that you wrote a file. The wrapper writes files after validating this content.
"""
    return f"""You are a bounded Claude worker.

Task:
{job.get('instruction', '')}

Allowed files:
{chr(10).join(files)}

Context:
{context}

Return this format:
STATUS: <SUCCESS|NEEDS_REPLAN|OVERFLOW_DETECTED|ROUTER_ERROR|CHILD_TIMEOUT|FAILED>
FILES: <comma-separated paths>
TESTS: <what was run or not-run>
SUMMARY: <short summary>

Do not output long reasoning. If you need broader scope, return NEEDS_REPLAN.
Do not claim that you used tools or wrote files. The wrapper verifies side effects.
"""


def main() -> int:
    if len(sys.argv) < 3:
        print("Usage: worker_claude_router.py <job.json> <worker-kind>", file=sys.stderr)
        return 2
    job_path = Path(sys.argv[1]).resolve()
    worker_kind = sys.argv[2]
    job = read_json(job_path)
    run_dir = job_path.parent.parent
    results_dir = run_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    job_id = str(job["id"])
    raw_file = results_dir / f"{job_id}.raw.txt"
    exec_log_file = results_dir / f"{job_id}.exec.log"
    status_file = results_dir / f"{job_id}.status.json"
    test_output_file = results_dir / f"{job_id}.test.txt"
    scope = Path(str(job.get("scope_path") or run_dir / "worktree")).resolve()
    files = [str(item) for item in job.get("files", [])]
    before = file_state(scope, files)
    start = time.time()
    prompt = build_prompt(job, worker_kind, scope)
    exit_code, raw = call_ccr(prompt, int(os.environ.get("CLAUDE_CHILD_TIMEOUT_SEC", "120")))
    raw_file.write_text(raw, encoding="utf-8")
    exec_log_file.write_text(raw, encoding="utf-8")
    status = status_from_raw(raw, exit_code)
    if status == "SUCCESS" and worker_kind in {"summary", "managed"} and files:
        target = scope / files[0]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(extract_summary_content(raw), encoding="utf-8")
    after = file_state(scope, files)
    actual_changed_files = changed_files(before, after)
    pseudo_tool_call_detected = looks_like_pseudo_tool_call(raw)
    test_command = str(job.get("test_command") or "")
    test_exit_code = 0
    if test_command:
        proc = subprocess.run(test_command, shell=True, cwd=scope, text=True, capture_output=True)
        test_exit_code = proc.returncode
        test_output_file.write_text((proc.stdout or "") + (proc.stderr or ""), encoding="utf-8")
    else:
        test_output_file.write_text("not-run\n", encoding="utf-8")
    failed_reason = ""
    if status == "SUCCESS" and pseudo_tool_call_detected:
        status = "PSEUDO_TOOL_CALL_DETECTED"
    if status == "SUCCESS" and bool(job.get("require_change")) and not actual_changed_files:
        status = "FAILED"
        failed_reason = "required file did not change"
    if status == "SUCCESS" and test_command and test_exit_code != 0:
        status = "FAILED"
        failed_reason = "test command failed"
    verification_note = "verified"
    if status == "ROUTER_ERROR":
        verification_note = "router transport or upstream failure blocked the child request"
    elif status == "CHILD_TIMEOUT":
        verification_note = "child worker timed out"
    elif status == "OVERFLOW_DETECTED":
        verification_note = "child worker reported context/output pressure"
    elif status == "FAILED":
        verification_note = failed_reason or "worker output did not satisfy change/test contract"
    elif status == "PSEUDO_TOOL_CALL_DETECTED":
        verification_note = "model returned a pseudo tool call, but no executable side effect was verified"
    payload = {
        "id": job_id,
        "status": status,
        "scope_path": str(scope),
        "require_change": bool(job.get("require_change")),
        "files": files,
        "owner_role": job.get("owner_role", ""),
        "actual_changed_files": actual_changed_files,
        "actual_changed_count": len(actual_changed_files),
        "pseudo_tool_call_detected": pseudo_tool_call_detected,
        "failure_family": (
            "pseudo_tool_call" if status == "PSEUDO_TOOL_CALL_DETECTED" else "failed" if status == "FAILED" else "overflow" if status == "OVERFLOW_DETECTED" else "router" if status == "ROUTER_ERROR" else "timeout" if status == "CHILD_TIMEOUT" else ""
        ),
        "verification_note": verification_note,
        "raw_file": str(raw_file),
        "exec_log_file": str(exec_log_file),
        "success_check": job.get("success_check", ""),
        "test_command": test_command,
        "test_executed_command": test_command,
        "test_output_file": str(test_output_file),
        "test_exit_code": test_exit_code,
        "exit_code": exit_code,
        "duration_sec": round(time.time() - start, 3),
        "key_claims": [{"claim": verification_note if status != "SUCCESS" else "Worker result is bounded to assigned files and CCR output.", "evidence_refs": [str(raw_file), *[str(scope / rel) for rel in files]]}],
        "confidence": "medium" if status == "SUCCESS" else "low",
        "limitations": ["Live worker quality depends on configured CCR model and endpoint."],
        "handoff_next": "Review status, raw output, and claim ledger before accepting.",
    }
    for key in ["agent_profile", "profile_mode", "effective_policy_level", "effective_allowed_skills", "effective_allowed_mcp_groups", "effective_tool_policy", "profile_settings_overlay", "profile_scope_limits", "profile_recovery_policy"]:
        if key in job:
            payload[key] = job[key]
    write_json(status_file, payload)
    print(raw)
    return 0 if status == "SUCCESS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
