from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from agent_token_ledger import token_usage_from_text
from bounded_context_loader import build_bounded_context, context_budget_for, load_context_defaults


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def file_state(scope: Path, files: list[str]) -> dict[str, dict[str, Any]]:
    state: dict[str, dict[str, Any]] = {}
    for rel in files:
        path = scope / rel
        entry: dict[str, Any] = {"exists": path.exists()}
        if path.exists() and path.is_file():
            entry["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        state[rel] = entry
    return state


def changed_files(before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]]) -> list[str]:
    return [path for path, prev in before.items() if prev != after.get(path, {})]


def call_ccr(prompt: str, timeout: int) -> tuple[int, str, dict[str, Any], str]:
    url = os.environ.get("CCR_MESSAGES_URL", "http://127.0.0.1:3456/v1/messages")
    model = os.environ.get("CLAUDE_MODEL_ALIAS") or os.environ.get("CCR_PREFERRED_MODEL")
    if not model:
        return 1, "STATUS: ROUTER_ERROR\nFILES:\nTESTS: not-run\nSUMMARY: CLAUDE_MODEL_ALIAS or CCR_PREFERRED_MODEL is required for ccr_http worker transport\n", {}, "ccr_http"
    api_key = os.environ.get("CCR_API_KEY", os.environ.get("ANTHROPIC_API_KEY", "local-router-token"))
    max_tokens = int(os.environ.get("CCR_MAX_OUTPUT_TOKENS", "1024"))
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        return 1, f"STATUS: ROUTER_ERROR\nFILES:\nTESTS: not-run\nSUMMARY: HTTP {exc.code} from CCR\n\n{detail[:1200]}\n", {}, "ccr_http"
    except TimeoutError:
        return 124, "STATUS: CHILD_TIMEOUT\nFILES:\nTESTS: not-run\nSUMMARY: CCR request timed out\n", {}, "ccr_http"
    except Exception as exc:
        return 1, f"STATUS: ROUTER_ERROR\nFILES:\nTESTS: not-run\nSUMMARY: {exc}\n", {}, "ccr_http"
    if not body.strip():
        return 1, "STATUS: ROUTER_ERROR\nFILES:\nTESTS: not-run\nSUMMARY: empty response from CCR\n", {}, "ccr_http"
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return 0, body, {}, "ccr_http"
    usage = parsed.get("usage") if isinstance(parsed.get("usage"), dict) else {}
    if isinstance(parsed.get("content"), list):
        text = "\n".join(str(item.get("text", "")) for item in parsed["content"] if isinstance(item, dict))
        return 0, text or body, usage, "ccr_http"
    if isinstance(parsed.get("choices"), list) and parsed["choices"]:
        message = parsed["choices"][0].get("message", {})
        return 0, str(message.get("content") or parsed["choices"][0].get("text") or body), usage, "ccr_http"
    return 0, body, usage, "ccr_http"


def call_claude_cli(prompt: str, timeout: int) -> tuple[int, str, dict[str, Any], str]:
    claude_bin = os.environ.get("AI_COMPANY_CLAUDE_BIN", "claude").strip() or "claude"
    command = [
        claude_bin,
        "--bare",
        "-p",
        "--output-format",
        "json",
        "--tools",
        "",
        "--system-prompt",
        "You are a bounded worker. Use only supplied context, do not use tools, and return only the requested artifact content or status contract.",
        prompt,
    ]
    try:
        completed = subprocess.run(command, text=True, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return 124, "STATUS: CHILD_TIMEOUT\nFILES:\nTESTS: not-run\nSUMMARY: Claude CLI request timed out\n", {}, "claude_cli"
    except OSError as exc:
        return 1, f"STATUS: ROUTER_ERROR\nFILES:\nTESTS: not-run\nSUMMARY: Claude CLI unavailable: {exc}\n", {}, "claude_cli"
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        return completed.returncode, f"STATUS: ROUTER_ERROR\nFILES:\nTESTS: not-run\nSUMMARY: Claude CLI exited {completed.returncode}: {detail[:1200]}\n", {}, "claude_cli"
    try:
        envelope = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return 1, "STATUS: ROUTER_ERROR\nFILES:\nTESTS: not-run\nSUMMARY: Claude CLI returned malformed JSON envelope\n", {}, "claude_cli"
    if not isinstance(envelope, dict):
        return 1, "STATUS: ROUTER_ERROR\nFILES:\nTESTS: not-run\nSUMMARY: Claude CLI returned invalid JSON envelope\n", {}, "claude_cli"
    raw = str(envelope.get("result") or envelope.get("text") or envelope.get("output") or "")
    usage = envelope.get("usage") if isinstance(envelope.get("usage"), dict) else {}
    if not raw.strip():
        return 1, "STATUS: ROUTER_ERROR\nFILES:\nTESTS: not-run\nSUMMARY: Claude CLI returned empty result\n", usage, "claude_cli"
    return 0, raw, usage, "claude_cli"


def call_worker_provider(prompt: str, timeout: int) -> tuple[int, str, dict[str, Any], str]:
    transport = os.environ.get("AI_COMPANY_LIVE_WORKER_TRANSPORT", "auto").strip().lower() or "auto"
    if transport == "auto":
        claude_bin = os.environ.get("AI_COMPANY_CLAUDE_BIN", "claude").strip() or "claude"
        transport = "claude_cli" if shutil.which(claude_bin) or Path(claude_bin).is_file() else "ccr_http"
    if transport == "claude_cli":
        return call_claude_cli(prompt, timeout)
    if transport == "ccr_http":
        return call_ccr(prompt, timeout)
    return 1, f"STATUS: ROUTER_ERROR\nFILES:\nTESTS: not-run\nSUMMARY: unsupported worker transport: {transport}\n", {}, transport


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


def extract_multi_artifacts(raw: str, expected_paths: list[str]) -> dict[str, str]:
    match = re.search(r"ARTIFACTS_JSON_START\s*(.*?)\s*ARTIFACTS_JSON_END", raw, flags=re.S)
    if not match:
        raise ValueError("multi-file response is missing ARTIFACTS_JSON_START/END")
    payload, consumed = json.JSONDecoder().raw_decode(match.group(1).lstrip())
    if match.group(1).lstrip()[consumed:].strip():
        raise ValueError("unexpected content after multi-file JSON envelope")
    artifacts = payload.get("artifacts", []) if isinstance(payload, dict) else []
    actual: dict[str, str] = {}
    for item in artifacts:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not isinstance(item.get("content"), str):
            raise ValueError("each multi-file artifact requires string path and content")
        path = item["path"]
        if path in actual:
            raise ValueError(f"duplicate artifact path: {path}")
        actual[path] = item["content"]
    if set(actual) != set(expected_paths):
        raise ValueError(f"artifact paths mismatch: expected={sorted(expected_paths)} actual={sorted(actual)}")
    return actual


def build_prompt_details(job: dict[str, Any], worker_kind: str, scope: Path) -> tuple[str, dict[str, Any]]:
    files = job.get("files", [])
    context_files = [str(item) for item in job.get("template_context_files", [])]
    context_targets = [str(item) for item in job.get("inputs", [])] if job.get("capability") else [str(item) for item in files]
    if worker_kind == "summary":
        for rel in ["evidence_summary.txt", "summary_context.txt"]:
            if rel not in context_files and (scope / rel).exists():
                context_files.append(rel)
    role = str(job.get("agent_profile") or job.get("owner_role") or "default")
    defaults = load_context_defaults()
    context_result = build_bounded_context(
        scope,
        [*context_files, *context_targets],
        role=role,
        defaults=defaults,
    )
    context_blocks = [context_result["text"]]
    requirements_path = scope / "artifact_requirements.json"
    verifier_contract = ""
    if worker_kind == "summary" and requirements_path.exists():
        requirements_result = build_bounded_context(
            scope,
            ["artifact_requirements.json"],
            role="reviewer_worker",
            defaults=defaults,
            extra_budget_tokens=1000,
        )
        requirements_text = requirements_result["text"]
        context_blocks.append(f"Verifier contract: artifact_requirements.json\n{requirements_text}")
        try:
            requirements = json.loads(requirements_path.read_text(encoding="utf-8", errors="ignore"))
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
    manifest_lines = []
    for item in context_result.get("manifest", []):
        manifest_lines.append(
            f"- {item.get('path')}: {item.get('status', 'unknown')}; "
            f"bytes={item.get('size_bytes', 0)}; included_chars={item.get('included_chars', 0)}; "
            f"skipped_bytes={item.get('skipped_bytes', 0)}"
        )
    context_manifest = "\n".join(manifest_lines) or "- no files"
    budget = context_budget_for(role, defaults)
    if worker_kind == "summary":
        prompt = f"""You are a strict synthesis worker.

Task:
{job.get("instruction", "")}

Evidence and target context:
{context}
{verifier_contract}

Context guard manifest (do not assume omitted bytes were read):
{context_manifest}
Input budget: {budget['input_tokens']} estimated tokens.

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
    elif worker_kind == "managed":
        target = files[0] if files else "output.txt"
        suffix = Path(target).suffix.lower()
        format_rule = "Return concise UTF-8 text."
        if suffix == ".json":
            format_rule = "Return one valid JSON object only. Do not use markdown fences. Include concrete evidence fields and no comments."
        elif suffix in {".py", ".js", ".sh"}:
            format_rule = "Return executable source code only. Do not use markdown fences or explanations."
        elif suffix in {".md", ".txt"}:
            format_rule = "Return concise evidence-grounded document content with an explicit conclusion and limitations."
        prompt = f"""You are a bounded managed-artifact worker.

Task:
{job.get("instruction", "")}

Target artifact: {target}
Declared inputs: {json.dumps(job.get("inputs", []), ensure_ascii=False)}
Acceptance criteria: {json.dumps(job.get("acceptance_criteria", []), ensure_ascii=False)}

Context:
{context}

Context guard manifest (do not assume omitted bytes were read):
{context_manifest}
Input budget: {budget['input_tokens']} estimated tokens.

{format_rule}
Wrap the artifact exactly as:
CONTENT_START
<artifact content>
CONTENT_END

Do not claim to call tools. The wrapper writes one declared artifact and runs the declared test command.
Any declared input shown as present in the Context guard manifest is available. Do not return NEEDS_REPLAN for an input that is present; use its supplied Context content.
If required input is missing, return STATUS: NEEDS_REPLAN instead of fabricating evidence.
"""
    elif worker_kind == "multi":
        prompt = f"""You are a bounded managed multi-artifact worker.

Task:
{job.get("instruction", "")}

Declared outputs: {json.dumps(files, ensure_ascii=False)}
Declared inputs: {json.dumps(job.get("inputs", []), ensure_ascii=False)}
Acceptance criteria: {json.dumps(job.get("acceptance_criteria", []), ensure_ascii=False)}

Context:
{context}

Return exactly:
STATUS: SUCCESS
ARTIFACTS_JSON_START
{{"artifacts":[{{"path":"<first exact declared path>","content":"<complete content>"}},{{"path":"<second exact declared path>","content":"<complete content>"}}]}}
ARTIFACTS_JSON_END

Return every declared output exactly once and no other path. Use valid JSON escaping.
Do not claim tool use. The wrapper validates paths and atomically writes the files.
If evidence is insufficient, return STATUS: NEEDS_REPLAN instead of fabricating it.
"""
    else:
        prompt = f"""You are a bounded Claude worker.

Task:
{job.get("instruction", "")}

Allowed files:
{chr(10).join(files)}

Context:
{context}

Context guard manifest (do not assume omitted bytes were read):
{context_manifest}
Input budget: {budget['input_tokens']} estimated tokens.

Return this format:
STATUS: <SUCCESS|NEEDS_REPLAN|OVERFLOW_DETECTED|ROUTER_ERROR|CHILD_TIMEOUT|FAILED>
FILES: <comma-separated paths>
TESTS: <what was run or not-run>
SUMMARY: <short summary>

Do not output long reasoning. If you need broader scope, return NEEDS_REPLAN.
Do not claim that you used tools or wrote files. The wrapper verifies side effects.
"""
    context_result["role"] = role
    context_result["budget"] = budget
    context_result["context_manifest_text"] = context_manifest
    return prompt, context_result


def build_prompt(job: dict[str, Any], worker_kind: str, scope: Path) -> str:
    return build_prompt_details(job, worker_kind, scope)[0]


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
    prompt, context_info = build_prompt_details(job, worker_kind, scope)
    guard_errors = context_info.get("errors", [])
    hard_context_tokens = int(load_context_defaults().get("context_hard_tokens_per_turn", 18000))
    estimated_input_tokens = int(context_info.get("estimated_input_tokens", 0))
    blocked_before_model = bool(guard_errors) or estimated_input_tokens > hard_context_tokens
    if blocked_before_model:
        exit_code = 1
        provider_usage = {}
        transport = "context_guard"
        reasons = []
        for item in guard_errors:
            reasons.append(f"{item.get('path', '<unknown>')}: {item.get('status', 'context error')}")
        if estimated_input_tokens > hard_context_tokens:
            reasons.append(f"estimated input {estimated_input_tokens} > hard limit {hard_context_tokens} tokens")
        raw = (
            "STATUS: OVERFLOW_DETECTED\nFILES:\nTESTS: not-run\nSUMMARY: "
            "context guard blocked model call before provider execution\n"
            + "\n".join(reasons)
            + "\n"
        )
    else:
        exit_code, raw, provider_usage, transport = call_worker_provider(
            prompt,
            int(os.environ.get("CLAUDE_CHILD_TIMEOUT_SEC", "120")),
        )
    raw_file.write_text(raw, encoding="utf-8")
    exec_log_file.write_text(raw, encoding="utf-8")
    status = status_from_raw(raw, exit_code)
    failed_reason = ""
    if status == "SUCCESS" and worker_kind in {"summary", "managed"} and files:
        target = (scope / files[0]).resolve()
        try:
            target.relative_to(scope)
        except ValueError:
            status = "FAILED"
            failed_reason = f"output path escapes run scope: {files[0]}"
            target = scope / ".blocked-output"
        target.parent.mkdir(parents=True, exist_ok=True)
        if status == "SUCCESS":
            target.write_text(extract_summary_content(raw), encoding="utf-8")
    if status == "SUCCESS" and worker_kind == "multi":
        try:
            artifacts = extract_multi_artifacts(raw, files)
            staged: list[tuple[Path, Path]] = []
            for rel, content in artifacts.items():
                target = (scope / rel).resolve()
                target.relative_to(scope)
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(target.name + f".tmp-{job_id}")
                temporary.write_text(content, encoding="utf-8")
                staged.append((temporary, target))
            for temporary, target in staged:
                temporary.replace(target)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            status = "FAILED"
            failed_reason = f"multi-file contract failed: {exc}"
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
            "pseudo_tool_call"
            if status == "PSEUDO_TOOL_CALL_DETECTED"
            else "failed"
            if status == "FAILED"
            else "overflow"
            if status == "OVERFLOW_DETECTED"
            else "router"
            if status == "ROUTER_ERROR"
            else "timeout"
            if status == "CHILD_TIMEOUT"
            else ""
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
        "transport": transport,
        "agent_profile": job.get("agent_profile", ""),
        "profile_mode": job.get("profile_mode", ""),
        "context_guard": {
            "input_budget_tokens": context_info.get("budget", {}).get("input_tokens", 0),
            "estimated_input_tokens": context_info.get("estimated_input_tokens", 0),
            "context_budget_exceeded": context_info.get("context_budget_exceeded", False),
            "manifest": context_info.get("manifest", []),
            "skipped_bytes": context_info.get("skipped_bytes", 0),
            "actions": context_info.get("context_guard_actions", []),
            "blocked_before_model": blocked_before_model,
            "block_reasons": guard_errors,
        },
        "token_usage": token_usage_from_text(prompt, raw, provider_usage),
        "key_claims": [
            {
                "claim": verification_note if status != "SUCCESS" else "Worker result is bounded to assigned files and CCR output.",
                "evidence_refs": [str(raw_file), *[str(scope / rel) for rel in files]],
            }
        ],
        "confidence": "medium" if status == "SUCCESS" else "low",
        "limitations": ["Live worker quality depends on configured CCR model and endpoint."],
        "handoff_next": "Review status, raw output, and claim ledger before accepting.",
    }
    for key in [
        "agent_profile",
        "profile_mode",
        "effective_policy_level",
        "effective_allowed_skills",
        "effective_allowed_mcp_groups",
        "effective_tool_policy",
        "profile_settings_overlay",
        "profile_scope_limits",
        "profile_recovery_policy",
    ]:
        if key in job:
            payload[key] = job[key]
    write_json(status_file, payload)
    print(raw)
    return 0 if status == "SUCCESS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
