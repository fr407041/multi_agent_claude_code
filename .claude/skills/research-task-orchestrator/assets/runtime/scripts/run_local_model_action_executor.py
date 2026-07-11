#!/usr/bin/env python3
"""Deterministic action executor for local-model live tasks.

The local model is responsible for proposing the actions. This runner only
validates and executes a small allowlisted action manifest, then emits the same
artifact shape the dashboard/watchdog already know how to read.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_token_ledger import build_agent_token_ledger, token_usage_from_text


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_ROOT = ROOT / "results" / "ai_company_task_harness"
ALLOWED_ACTIONS = {"write_file", "read_file", "run_command", "finish"}
DEFAULT_ALLOWED_COMMANDS = {"python3", "python", "curl", "sed", "awk", "grep", "find", "mkdir", "cat", "head", "tail"}
SOURCE_SUFFIXES = {".py", ".js", ".ts", ".sh", ".go", ".rs", ".java"}
REPORT_SUFFIXES = {".md", ".html", ".htm"}
_LAST_TRANSPORT_METADATA: dict[str, Any] = {}


class ActionExecutionError(RuntimeError):
    def __init__(
        self,
        message: str,
        action_log: list[dict[str, Any]] | None = None,
        generated_files: list[str] | None = None,
        final_summary: str = "",
    ) -> None:
        super().__init__(message)
        self.action_log = action_log or []
        self.generated_files = generated_files or []
        self.final_summary = final_summary


class ManifestTransportError(ValueError):
    def __init__(self, message: str, category: str, raw_response: str, metadata: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.category = category
        self.raw_response = raw_response
        self.metadata = metadata or {}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_json_payload(text: str) -> dict[str, Any]:
    stripped = text.lstrip("\ufeff").strip()
    fence = re.fullmatch(r"```(?:json)?\s*\n(.*?)\n```", stripped, flags=re.S | re.I)
    if fence:
        stripped = fence.group(1).strip()
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON action manifest: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("action manifest must be a JSON object")
    return payload


def ensure_within(root: Path, rel_path: str) -> Path:
    if not rel_path or Path(rel_path).is_absolute():
        raise ValueError(f"path must be relative to run worktree: {rel_path!r}")
    target = (root / rel_path).resolve()
    root_resolved = root.resolve()
    if target != root_resolved and root_resolved not in target.parents:
        raise ValueError(f"path escapes run worktree: {rel_path}")
    return target


def extract_task_api_text(payload: dict[str, Any]) -> str:
    for key in ["result_text", "text", "output", "response", "content", "message"]:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    result = payload.get("result")
    if isinstance(result, dict):
        return extract_task_api_text(result)
    return result if isinstance(result, str) and result.strip() else ""


def extract_action_manifest_from_task_output(text: str) -> str:
    """Extract an action manifest from task wrappers that prepend command logs."""
    decoder = json.JSONDecoder()
    matches: list[dict[str, Any]] = []
    for match in re.finditer(r"\{", text):
        try:
            candidate, _ = decoder.raw_decode(text[match.start() :])
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict) and isinstance(candidate.get("actions"), list):
            matches.append(candidate)
    if not matches:
        looks_truncated = '"actions"' in text or text.count("{") > text.count("}") or text.rstrip().endswith(("{", "[", ","))
        category = "manifest_truncated" if looks_truncated else "manifest_contract_invalid"
        raise ManifestTransportError(
            "live task API output did not contain a complete action manifest",
            category,
            text,
        )
    return json.dumps(matches[-1], ensure_ascii=False)


def _file_snapshot(root: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for path in root.rglob("*"):
        if path.is_file():
            snapshot[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snapshot


def _artifact_batches(paths: list[str], limit: int, max_stages: int) -> list[list[str]]:
    def priority(value: str) -> tuple[int, str]:
        suffix = Path(value).suffix.lower()
        return (0 if suffix in SOURCE_SUFFIXES else 2 if suffix in REPORT_SUFFIXES else 1, value)

    ordered = sorted(dict.fromkeys(paths), key=priority)
    batches = [ordered[index : index + limit] for index in range(0, len(ordered), limit)] or [[]]
    if len(batches) > max_stages:
        raise ValueError(f"staged execution requires {len(batches)} stages, above max_stages={max_stages}")
    return batches


def derive_task_status_url(task_url: str, run_id: str) -> str:
    explicit = os.environ.get("AI_COMPANY_LIVE_TASK_STATUS_URL", "").strip()
    if explicit:
        return explicit.replace("{run_id}", run_id)
    base = task_url.rstrip("/")
    return (base[: -len("/run-task")] if base.endswith("/run-task") else base) + f"/runs/{run_id}"


def call_live_task_api(prompt: str, timeout_sec: int) -> str:
    global _LAST_TRANSPORT_METADATA
    url = os.environ.get("AI_COMPANY_LIVE_TASK_URL", "").strip()
    payload = {"task": prompt, "timeout_seconds": timeout_sec}
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_sec) as response:
        created = json.loads(response.read().decode("utf-8", errors="ignore"))
    text = extract_task_api_text(created)
    status = str(created.get("status", "")).lower()
    run_id = str(created.get("run_id", ""))
    _LAST_TRANSPORT_METADATA = {"nested_run_id": run_id, "transport_status": status, "response_chars": len(text)}
    pending = {"queued", "running", "pending", "in_progress"}
    if text and status not in pending:
        try:
            return extract_action_manifest_from_task_output(text)
        except ManifestTransportError as exc:
            exc.metadata.update(_LAST_TRANSPORT_METADATA)
            raise
    if not run_id:
        raise RuntimeError("live task API returned no result_text and no run_id for polling")
    status_url = derive_task_status_url(url, run_id)
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        time.sleep(float(os.environ.get("AI_COMPANY_LIVE_TASK_POLL_INTERVAL_SEC", "2")))
        with urllib.request.urlopen(
            urllib.request.Request(status_url, headers={"Accept": "application/json"}, method="GET"),
            timeout=min(30, timeout_sec),
        ) as response:
            polled = json.loads(response.read().decode("utf-8", errors="ignore"))
        text = extract_task_api_text(polled)
        status = str(polled.get("status", status)).lower()
        _LAST_TRANSPORT_METADATA = {"nested_run_id": run_id, "transport_status": status, "response_chars": len(text)}
        if text and status not in pending:
            try:
                return extract_action_manifest_from_task_output(text)
            except ManifestTransportError as exc:
                exc.metadata.update(_LAST_TRANSPORT_METADATA)
                raise
        if status in {"failed", "error", "timeout", "cancelled"}:
            raise RuntimeError(f"live task API run {run_id} ended with status {status}: {text[:500]}")
    raise TimeoutError(f"live task API run {run_id} did not finish before {timeout_sec}s")


def call_ccr_for_manifest(task: str, timeout_sec: int, repair_feedback: str = "") -> str:
    prompt = f"""Return only strict JSON. No markdown. No explanation.

You are a local-model action planner. Produce a JSON object with an actions array.
Allowed action types:
- write_file: {{"type":"write_file","path":"relative/path","content":"..."}}
- read_file: {{"type":"read_file","path":"relative/path"}}
- run_command: {{"type":"run_command","command":["python3","script.py"],"timeout_sec":60}}
- finish: {{"type":"finish","summary":"...","artifacts":["relative/path"]}}

Rules:
- All paths must be relative to the run worktree.
- Do not write outside the run worktree.
- Do not edit dashboard, repo scripts, or global config.
- Prefer small, verifiable steps.

Task:
{task}
"""
    if repair_feedback:
        prompt += f"""

Previous attempt failed. Produce a corrected full action manifest.
Failure feedback:
{repair_feedback}
"""
    if os.environ.get("AI_COMPANY_LIVE_TASK_URL", "").strip():
        return call_live_task_api(prompt, timeout_sec)

    url = os.environ.get("CCR_MESSAGES_URL", "http://127.0.0.1:3456/v1/messages")
    model = os.environ.get("CLAUDE_MODEL_ALIAS") or os.environ.get("CCR_PREFERRED_MODEL")
    if not model:
        raise RuntimeError(
            "set AI_COMPANY_LIVE_TASK_URL for provider-neutral execution, or set CLAUDE_MODEL_ALIAS/CCR_PREFERRED_MODEL for ccr_http"
        )
    api_key = os.environ.get("CCR_API_KEY", os.environ.get("ANTHROPIC_API_KEY", "local-router-token"))
    max_tokens = int(os.environ.get("CCR_MAX_OUTPUT_TOKENS", "2048"))
    payload = {"model": model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]}
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-api-key": api_key, "anthropic-version": "2023-06-01"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_sec) as response:
        body = response.read().decode("utf-8", errors="ignore")
    parsed = json.loads(body)
    if isinstance(parsed.get("content"), list):
        return "\n".join(str(item.get("text", "")) for item in parsed["content"] if isinstance(item, dict))
    if isinstance(parsed.get("choices"), list) and parsed["choices"]:
        message = parsed["choices"][0].get("message", {})
        return str(message.get("content") or parsed["choices"][0].get("text") or body)
    return body


def execute_manifest(
    manifest: dict[str, Any],
    run_dir: Path,
    allowed_commands: set[str],
    max_actions: int,
) -> tuple[list[dict[str, Any]], list[str], str, dict[str, dict[str, Any]]]:
    worktree = run_dir / "worktree"
    worktree.mkdir(parents=True, exist_ok=True)
    actions = manifest.get("actions")
    if not isinstance(actions, list):
        raise ValueError("action manifest must contain actions: []")
    if len(actions) > max_actions:
        raise ValueError(f"too many actions: {len(actions)} > {max_actions}")
    action_log: list[dict[str, Any]] = []
    generated_files: list[str] = []
    provenance: dict[str, dict[str, Any]] = {}
    final_summary = ""
    for index, action in enumerate(actions, start=1):
        if not isinstance(action, dict):
            entry = {"index": index, "type": "invalid", "started_at": now_iso(), "finished_at": now_iso(), "status": "failed", "error": f"action {index} must be an object"}
            action_log.append(entry)
            raise ActionExecutionError(entry["error"], action_log, generated_files, final_summary)
        action_type = str(action.get("type") or "")
        if action_type not in ALLOWED_ACTIONS:
            entry = {"index": index, "type": action_type or "invalid", "started_at": now_iso(), "finished_at": now_iso(), "status": "failed", "error": f"unsupported action type: {action_type}"}
            action_log.append(entry)
            raise ActionExecutionError(entry["error"], action_log, generated_files, final_summary)
        entry: dict[str, Any] = {"index": index, "type": action_type, "started_at": now_iso()}
        if action_type == "write_file":
            target = ensure_within(worktree, str(action.get("path") or ""))
            content = str(action.get("content") or "")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            rel = str(target.relative_to(worktree).as_posix())
            generated_files.append(rel)
            provenance[rel] = {"action_type": "write_file", "action_index": index}
            entry.update({"path": rel, "bytes": len(content.encode("utf-8")), "status": "ok"})
        elif action_type == "read_file":
            target = ensure_within(worktree, str(action.get("path") or ""))
            if not target.exists() or not target.is_file():
                raise ValueError(f"read_file target missing: {action.get('path')}")
            entry.update({"path": str(target.relative_to(worktree).as_posix()), "chars": len(read_text(target)), "status": "ok"})
        elif action_type == "run_command":
            command = action.get("command")
            if not isinstance(command, list) or not command or not all(isinstance(item, str) for item in command):
                entry.update({"command": command, "status": "failed", "error": "run_command.command must be a non-empty string array", "finished_at": now_iso()})
                action_log.append(entry)
                raise ActionExecutionError(entry["error"], action_log, generated_files, final_summary)
            executable = Path(command[0]).name
            if executable not in allowed_commands:
                entry.update({"command": command, "status": "failed", "error": f"command not allowlisted: {command[0]}", "finished_at": now_iso()})
                action_log.append(entry)
                raise ActionExecutionError(entry["error"], action_log, generated_files, final_summary)
            timeout_sec = int(action.get("timeout_sec") or 60)
            before = _file_snapshot(worktree)
            proc = subprocess.run(command, cwd=worktree, text=True, capture_output=True, timeout=timeout_sec, check=False)
            after = _file_snapshot(worktree)
            changed_files = sorted(path for path, digest in after.items() if before.get(path) != digest)
            for rel in changed_files:
                generated_files.append(rel)
                provenance[rel] = {"action_type": "run_command", "action_index": index, "command": command}
            entry.update(
                {
                    "command": command,
                    "timeout_sec": timeout_sec,
                    "return_code": proc.returncode,
                    "stdout": proc.stdout[-4000:],
                    "stderr": proc.stderr[-4000:],
                    "status": "ok" if proc.returncode == 0 else "failed",
                    "changed_files": changed_files,
                }
            )
            if proc.returncode != 0:
                action_log.append(entry)
                raise ActionExecutionError(
                    f"command failed with exit code {proc.returncode}: {' '.join(command)}",
                    action_log,
                    generated_files,
                    final_summary,
                )
        elif action_type == "finish":
            final_summary = str(action.get("summary") or "")
            artifacts = action.get("artifacts") or []
            if not isinstance(artifacts, list):
                raise ValueError("finish.artifacts must be an array")
            for rel in artifacts:
                target = ensure_within(worktree, str(rel))
                if not target.exists():
                    raise ActionExecutionError(
                        f"declared artifact missing: {rel}",
                        action_log,
                        generated_files,
                        final_summary,
                    )
            entry.update({"summary": final_summary, "artifacts": artifacts, "status": "ok"})
        entry["finished_at"] = now_iso()
        action_log.append(entry)
    return action_log, sorted(set(generated_files)), final_summary, provenance


def validate_derived_artifacts(derived_artifacts: list[str], provenance: dict[str, dict[str, Any]]) -> list[str]:
    return [
        rel
        for rel in derived_artifacts
        if provenance.get(rel, {}).get("action_type") != "run_command"
    ]


def validate_expected_artifacts(run_dir: Path, expected_artifacts: list[str]) -> list[str]:
    worktree = run_dir / "worktree"
    missing = []
    for rel in expected_artifacts:
        try:
            target = ensure_within(worktree, rel)
        except ValueError:
            missing.append(rel)
            continue
        if not target.exists() or not target.is_file():
            missing.append(rel)
    return missing


def parse_expected_value(raw: str) -> Any:
    lowered = raw.strip().lower()
    if lowered in {"true", "false", "null"}:
        return json.loads(lowered)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def parse_json_expectation(spec: str) -> tuple[str, str, Any]:
    artifact, sep, rest = spec.partition(":")
    if not sep or not artifact or not rest:
        raise ValueError(f"expect-json-value must use FILE:PATH=VALUE format: {spec}")
    json_path, eq, raw_value = rest.partition("=")
    if not eq or not json_path:
        raise ValueError(f"expect-json-value must use FILE:PATH=VALUE format: {spec}")
    return artifact, json_path, parse_expected_value(raw_value)


def read_dot_path(payload: Any, dot_path: str) -> tuple[bool, Any]:
    current = payload
    for part in dot_path.split("."):
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, current


def values_equal(expected: Any, actual: Any) -> bool:
    if isinstance(expected, bool) or isinstance(actual, bool):
        return expected is actual
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return abs(float(expected) - float(actual)) <= 1e-9
    return expected == actual


def validate_json_expectations(run_dir: Path, expectation_specs: list[str]) -> list[dict[str, Any]]:
    worktree = run_dir / "worktree"
    results = []
    for spec in expectation_specs:
        artifact = ""
        json_path = ""
        expected: Any = None
        try:
            artifact, json_path, expected = parse_json_expectation(spec)
            target = ensure_within(worktree, artifact)
            if not target.exists() or not target.is_file():
                results.append(
                    {
                        "artifact": artifact,
                        "path": json_path,
                        "expected": expected,
                        "actual": None,
                        "status": "failed",
                        "reason": "artifact_missing",
                    }
                )
                continue
            payload = json.loads(target.read_text(encoding="utf-8"))
            found, actual = read_dot_path(payload, json_path)
            if not found:
                results.append(
                    {
                        "artifact": artifact,
                        "path": json_path,
                        "expected": expected,
                        "actual": None,
                        "status": "failed",
                        "reason": "path_missing",
                    }
                )
                continue
            status = "passed" if values_equal(expected, actual) else "failed"
            results.append(
                {
                    "artifact": artifact,
                    "path": json_path,
                    "expected": expected,
                    "actual": actual,
                    "status": status,
                    "reason": "" if status == "passed" else "value_mismatch",
                }
            )
        except Exception as exc:  # noqa: BLE001 - user-facing validator errors belong in artifacts.
            results.append(
                {
                    "artifact": artifact,
                    "path": json_path,
                    "expected": expected,
                    "actual": None,
                    "status": "failed",
                    "reason": str(exc),
                }
            )
    return results


def _load_worktree_json(run_dir: Path, artifact: str) -> Any:
    target = ensure_within(run_dir / "worktree", artifact)
    if not target.exists() or not target.is_file():
        raise ValueError(f"artifact missing: {artifact}")
    return json.loads(target.read_text(encoding="utf-8"))


def _json_differences(actual: Any, expected: Any, path: str = "$") -> list[dict[str, Any]]:
    differences: list[dict[str, Any]] = []
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [{"path": path, "reason": "type_mismatch", "expected": "object", "actual": value_type_name(actual)}]
        for key, expected_value in expected.items():
            child_path = f"{path}.{key}"
            if key not in actual:
                differences.append({"path": child_path, "reason": "missing_key", "expected": expected_value, "actual": None})
            else:
                differences.extend(_json_differences(actual[key], expected_value, child_path))
        for key in actual.keys() - expected.keys():
            differences.append({"path": f"{path}.{key}", "reason": "unexpected_key", "expected": None, "actual": actual[key]})
        return differences
    if isinstance(expected, list):
        if not isinstance(actual, list):
            return [{"path": path, "reason": "type_mismatch", "expected": "array", "actual": value_type_name(actual)}]
        if len(actual) != len(expected):
            differences.append({"path": path, "reason": "length_mismatch", "expected": len(expected), "actual": len(actual)})
        for index, (actual_value, expected_value) in enumerate(zip(actual, expected)):
            differences.extend(_json_differences(actual_value, expected_value, f"{path}[{index}]"))
        return differences
    if not values_equal(expected, actual):
        differences.append({"path": path, "reason": "value_mismatch", "expected": expected, "actual": actual})
    return differences


def _validate_schema_node(value: Any, schema: dict[str, Any], path: str = "$") -> list[dict[str, Any]]:
    defects: list[dict[str, Any]] = []
    expected_type = schema.get("type")
    type_matches = {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }
    if expected_type and not type_matches.get(str(expected_type), False):
        return [{"path": path, "reason": "schema_type_mismatch", "expected": expected_type, "actual": value_type_name(value)}]
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                defects.append({"path": f"{path}.{key}", "reason": "schema_required_missing", "expected": "present", "actual": None})
        for key, child_schema in schema.get("properties", {}).items():
            if key in value and isinstance(child_schema, dict):
                defects.extend(_validate_schema_node(value[key], child_schema, f"{path}.{key}"))
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            defects.extend(_validate_schema_node(item, schema["items"], f"{path}[{index}]"))
    return defects


def _parse_file_path(spec: str, option: str) -> tuple[str, str]:
    artifact, sep, path = spec.partition(":")
    if not sep or not artifact or not path:
        raise ValueError(f"{option} must use FILE:PATH format: {spec}")
    return artifact, path


def _parse_invariant(spec: str) -> tuple[str, str, str, Any]:
    match = re.fullmatch(r"([^:]+):(.+?)(==|!=|>=|<=|>|<)(.+)", spec)
    if not match:
        raise ValueError(f"invariant must use FILE:PATH<OP>VALUE format: {spec}")
    return match.group(1), match.group(2), match.group(3), parse_expected_value(match.group(4))


def _compare_invariant(actual: Any, operator: str, expected: Any) -> bool:
    if operator == "==":
        return values_equal(expected, actual)
    if operator == "!=":
        return not values_equal(expected, actual)
    if isinstance(actual, bool) or isinstance(expected, bool):
        return False
    if not isinstance(actual, (int, float)) or not isinstance(expected, (int, float)):
        return False
    return {">": actual > expected, ">=": actual >= expected, "<": actual < expected, "<=": actual <= expected}[operator]


def validate_domain_contract(
    run_dir: Path,
    result_status_specs: list[str],
    result_pass_value: Any,
    compare_specs: list[str],
    required_specs: list[str],
    invariant_specs: list[str],
    schema_files: list[str],
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    defects: list[dict[str, Any]] = []

    def record(kind: str, artifact: str, path: str, passed: bool, **details: Any) -> None:
        item = {"kind": kind, "artifact": artifact, "path": path, "status": "passed" if passed else "failed", **details}
        checks.append(item)
        if not passed:
            defects.append(item)

    for spec in result_status_specs:
        try:
            artifact, path = _parse_file_path(spec, "result-status")
            payload = _load_worktree_json(run_dir, artifact)
            found, actual = read_dot_path(payload, path)
            record("result_status", artifact, path, found and values_equal(result_pass_value, actual), expected=result_pass_value, actual=actual, reason="" if found else "path_missing")
        except Exception as exc:  # noqa: BLE001
            record("result_status", spec.partition(":")[0], spec.partition(":")[2], False, expected=result_pass_value, actual=None, reason=str(exc))

    for spec in compare_specs:
        actual_file, sep, expected_file = spec.partition(":")
        try:
            if not sep or not actual_file or not expected_file:
                raise ValueError(f"compare-json must use ACTUAL:EXPECTED format: {spec}")
            differences = _json_differences(_load_worktree_json(run_dir, actual_file), _load_worktree_json(run_dir, expected_file))
            record("json_comparison", actual_file, "$", not differences, expected=expected_file, actual=actual_file, differences=differences[:100])
        except Exception as exc:  # noqa: BLE001
            record("json_comparison", actual_file, "$", False, expected=expected_file, actual=actual_file, reason=str(exc))

    for spec in required_specs:
        try:
            artifact, path = _parse_file_path(spec, "require-json-path")
            found, actual = read_dot_path(_load_worktree_json(run_dir, artifact), path)
            record("required_path", artifact, path, found, expected="present", actual=actual, reason="" if found else "path_missing")
        except Exception as exc:  # noqa: BLE001
            record("required_path", spec.partition(":")[0], spec.partition(":")[2], False, expected="present", actual=None, reason=str(exc))

    for spec in invariant_specs:
        try:
            artifact, path, operator, expected = _parse_invariant(spec)
            found, actual = read_dot_path(_load_worktree_json(run_dir, artifact), path)
            record("invariant", artifact, path, found and _compare_invariant(actual, operator, expected), expected={"operator": operator, "value": expected}, actual=actual, reason="" if found else "path_missing")
        except Exception as exc:  # noqa: BLE001
            record("invariant", spec.partition(":")[0], "", False, expected=spec, actual=None, reason=str(exc))

    for schema_file in schema_files:
        try:
            schema_payload = json.loads(Path(schema_file).resolve().read_text(encoding="utf-8"))
            mappings = schema_payload.get("artifacts") if isinstance(schema_payload, dict) else None
            if not isinstance(mappings, dict):
                artifact = schema_payload.get("artifact") if isinstance(schema_payload, dict) else None
                schema = schema_payload.get("schema") if isinstance(schema_payload, dict) else None
                mappings = {artifact: schema} if artifact and isinstance(schema, dict) else {}
            if not mappings:
                continue
            for artifact, schema in mappings.items():
                schema_defects = _validate_schema_node(_load_worktree_json(run_dir, artifact), schema)
                record("schema", artifact, "$", not schema_defects, expected=schema_file, actual=artifact, defects=schema_defects[:100])
        except Exception as exc:  # noqa: BLE001
            record("schema", schema_file, "$", False, expected="valid artifact schema mapping", actual=None, reason=str(exc))

    enabled = bool(result_status_specs or compare_specs or required_specs or invariant_specs or any(item["kind"] == "schema" for item in checks))
    passed_count = sum(1 for item in checks if item["status"] == "passed")
    return {
        "enabled": enabled,
        "status": ("pass" if not defects else "fail") if enabled else "not_configured",
        "score": (passed_count / len(checks)) if checks else None,
        "checks": checks,
        "defects": defects,
        "validator_source": "runner_owned",
    }


def value_type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def infer_json_fields(value: Any, prefix: str = "") -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for key in sorted(value):
            path = f"{prefix}.{key}" if prefix else str(key)
            child = value[key]
            fields.append({"path": path, "type": value_type_name(child), "example": child if not isinstance(child, (dict, list)) else None})
            fields.extend(infer_json_fields(child, path))
    elif isinstance(value, list):
        merged_keys: dict[str, Any] = {}
        scalar_types = sorted({value_type_name(item) for item in value[:20]})
        for item in value[:20]:
            if isinstance(item, dict):
                for key, child in item.items():
                    merged_keys.setdefault(key, child)
        fields.append({"path": prefix or "$", "type": "array", "item_types": scalar_types, "sample_count": min(len(value), 20)})
        for key in sorted(merged_keys):
            path = f"{prefix}.[].{key}" if prefix else f"[].{key}"
            child = merged_keys[key]
            fields.append({"path": path, "type": value_type_name(child), "example": child if not isinstance(child, (dict, list)) else None})
            fields.extend(infer_json_fields(child, path))
    return fields


def build_schema_context(run_dir: Path, seeded_files: list[str], schema_files: list[str]) -> dict[str, Any]:
    worktree = run_dir / "worktree"
    sources: list[dict[str, Any]] = []
    for rel in seeded_files:
        target = ensure_within(worktree, rel)
        if target.suffix.lower() != ".json" or not target.exists():
            continue
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
            sources.append({"source": rel, "kind": "seed_file", "fields": infer_json_fields(payload)[:80]})
        except Exception as exc:  # noqa: BLE001 - schema hints are best-effort diagnostics.
            sources.append({"source": rel, "kind": "seed_file", "error": str(exc)})
    for schema_file in schema_files:
        target = Path(schema_file).resolve()
        if not target.exists() or not target.is_file():
            sources.append({"source": schema_file, "kind": "schema_file", "error": "schema_file_missing"})
            continue
        try:
            text = target.read_text(encoding="utf-8", errors="ignore")
            source: dict[str, Any] = {"source": str(target), "kind": "schema_file", "excerpt": text[:2000]}
            if target.suffix.lower() == ".json":
                source["fields"] = infer_json_fields(json.loads(text))[:80]
            sources.append(source)
        except Exception as exc:  # noqa: BLE001
            sources.append({"source": str(target), "kind": "schema_file", "error": str(exc)})
    usable = [source for source in sources if source.get("fields") or source.get("excerpt")]
    return {
        "schema_context_available": bool(usable),
        "source_count": len(sources),
        "usable_source_count": len(usable),
        "sources": sources,
    }


def build_repair_feedback(
    error: str,
    action_log: list[dict[str, Any]],
    generated_files: list[str],
    expected_artifacts: list[str],
    missing_expected: list[str],
    semantic_results: list[dict[str, Any]] | None = None,
    schema_context: dict[str, Any] | None = None,
) -> str:
    failed_actions = [item for item in action_log if item.get("status") == "failed"]
    excerpts = []
    for item in failed_actions[-2:]:
        excerpts.append(
            json.dumps(
                {
                    "type": item.get("type"),
                    "command": item.get("command"),
                    "return_code": item.get("return_code"),
                    "stdout": item.get("stdout", "")[-1200:],
                    "stderr": item.get("stderr", "")[-1200:],
                },
                ensure_ascii=False,
            )
        )
    schema_context = schema_context or {"schema_context_available": False, "sources": []}
    return "\n".join(
        [
            f"error: {error}",
            f"generated_files: {generated_files}",
            f"expected_artifacts: {expected_artifacts}",
            f"missing_expected_artifacts: {missing_expected}",
            f"semantic_expectation_failures: {[item for item in (semantic_results or []) if item.get('status') == 'failed']}",
            "schema_context_for_repair:",
            json.dumps(schema_context, ensure_ascii=False)[:3000],
            "repair_instruction: Use the schema context above when semantic checks fail. Do not invent field names; read the seeded input fields exactly.",
            "failed_action_excerpts:",
            *excerpts,
        ]
    )[-6000:]


def seed_worktree(run_dir: Path, seed_specs: list[str]) -> list[str]:
    worktree = run_dir / "worktree"
    worktree.mkdir(parents=True, exist_ok=True)
    seeded = []
    for spec in seed_specs:
        src_text, sep, dest_text = spec.partition("=")
        if not sep:
            raise ValueError(f"seed-file must use SRC=DEST format: {spec}")
        src = Path(src_text).resolve()
        if not src.exists() or not src.is_file():
            raise ValueError(f"seed source file missing: {src}")
        dest = ensure_within(worktree, dest_text)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        seeded.append(str(dest.relative_to(worktree).as_posix()))
    return seeded


def build_artifacts(
    run_dir: Path,
    run_id: str,
    task: str,
    status: str,
    action_log: list[dict[str, Any]],
    generated_files: list[str],
    final_summary: str,
    error: str,
    attempt_count: int = 1,
    repair_rounds_used: int = 0,
    expected_artifacts: list[str] | None = None,
    missing_expected_artifacts: list[str] | None = None,
    seeded_files: list[str] | None = None,
    semantic_results: list[dict[str, Any]] | None = None,
    schema_context: dict[str, Any] | None = None,
    started_at: str = "",
    finished_at: str = "",
    failure_category: str = "",
    transport_attempts: list[dict[str, Any]] | None = None,
    artifact_provenance: dict[str, dict[str, Any]] | None = None,
    derived_artifact_failures: list[str] | None = None,
    domain_verdict: dict[str, Any] | None = None,
    execution_strategy: str = "single",
    stage_count: int = 1,
) -> dict[str, Any]:
    ai_dir = run_dir / "ai_company"
    results_dir = run_dir / "results"
    ai_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)
    raw_file = results_dir / "job-001.raw.txt"
    exec_log_file = results_dir / "job-001.exec.log"
    raw_file.write_text(final_summary or error or json.dumps(action_log, ensure_ascii=False, indent=2), encoding="utf-8")
    command_logs = [
        f"action={item.get('index', 'n/a')} command={json.dumps(item.get('command', []), ensure_ascii=False)} "
        f"return_code={item.get('return_code', 'n/a')} status={item.get('status', 'unknown')}\n"
        f"stdout:\n{item.get('stdout', '')}\n"
        f"stderr:\n{item.get('stderr', '')}"
        for item in action_log
        if item.get("type") == "run_command"
    ]
    exec_log_file.write_text("\n\n".join(command_logs) or "No run_command actions were executed.\n", encoding="utf-8")
    success_claim = final_summary or f"Deterministic action manifest generated {len(generated_files)} file(s): {', '.join(generated_files) or 'none'}."
    status_payload = {
        "id": "job-001",
        "status": status,
        "scope_path": str(run_dir / "worktree"),
        "require_change": True,
        "files": generated_files,
        "owner_role": "local_action_executor",
        "actual_changed_files": generated_files,
        "actual_changed_count": len(generated_files),
        "pseudo_tool_call_detected": False,
        "failure_family": "" if status == "SUCCESS" else (failure_category or "failed"),
        "verification_note": "deterministic action manifest executed" if status == "SUCCESS" else error,
        "raw_file": str(raw_file),
        "exec_log_file": str(exec_log_file),
        "exit_code": 0 if status == "SUCCESS" else 1,
        "token_usage": token_usage_from_text(task, final_summary or error or json.dumps(action_log, ensure_ascii=False)),
        "key_claims": [
            {
                "claim": success_claim if status == "SUCCESS" else ("action executor failed: " + error),
                "evidence_refs": [str(raw_file), *[str(run_dir / "worktree" / rel) for rel in generated_files]],
            }
        ],
        "confidence": "medium" if status == "SUCCESS" else "low",
        "agent_profile": "local_action_executor",
        "profile_mode": "strict",
        "effective_policy_level": "enforced-by-runner + verified-posthoc",
        "effective_tool_policy": "allowlisted-actions-only",
    }
    write_json(results_dir / "job-001.status.json", status_payload)
    write_json(results_dir / "job-001.action_log.json", {"run_id": run_id, "actions": action_log, "error": error})
    write_json(ai_dir / "artifact_provenance.json", artifact_provenance or {})
    write_json(ai_dir / "transport_attempts.json", {"attempts": transport_attempts or []})
    domain_verdict = domain_verdict or {"enabled": False, "status": "not_configured", "score": None, "checks": [], "defects": [], "validator_source": "runner_owned"}
    write_json(ai_dir / "domain_verdict.json", domain_verdict)
    meeting = {
        "run_id": run_id,
        "meeting_status": "MEETING_READY",
        "rounds_used": 1,
        "run_profile_mode": "strict",
        "task_assignments": [{"task_id": "job-001", "owner_role": "local_action_executor", "agent_profile": "local_action_executor", "state": "done" if status == "SUCCESS" else "failed"}],
    }
    write_json(ai_dir / "meeting_decision.json", meeting)
    claim_ledger = {
        "run_id": run_id,
        "claims": status_payload["key_claims"],
        "metrics": {"claim_count": 1, "claim_coverage_rate": 1.0 if generated_files or raw_file.exists() else 0.0, "uncertainty_gap_count": 0},
    }
    reviewer = {
        "run_id": run_id,
        "verdict_count": 1,
        "accepted_count": 1 if status == "SUCCESS" else 0,
        "false_success_blocked_count": 0 if status == "SUCCESS" else 1,
        "profile_policy_violation_count": 0,
        "claim_ledger_metrics": claim_ledger["metrics"],
        "claim_contract": {"accepted_tasks_have_claim": bool(status_payload["key_claims"]), "all_claims_have_evidence": True},
        "verdicts": [{"task_id": "job-001", "agent_profile": "local_action_executor", "verdict": "ACCEPTED" if status == "SUCCESS" else "REPAIR_REQUIRED", "profile_policy_status": "ok", "profile_policy_notes": status_payload["verification_note"]}],
    }
    write_json(ai_dir / "subagent_claim_ledger.json", claim_ledger)
    write_json(ai_dir / "reviewer_verdicts.json", reviewer)
    expected_artifacts = expected_artifacts or []
    missing_expected_artifacts = missing_expected_artifacts or []
    seeded_files = seeded_files or []
    semantic_results = semantic_results or []
    schema_context = schema_context or {"schema_context_available": False, "sources": []}
    semantic_failed = [item for item in semantic_results if item.get("status") == "failed"]
    artifact = {
        "parsed": {
            "all_passed": status == "SUCCESS",
            "score": 1.0 if status == "SUCCESS" else 0.0,
            "checks": {
                "generated_file_exists": bool(generated_files),
                "executor_success": status == "SUCCESS",
                "expected_artifacts_present": not missing_expected_artifacts,
                "semantic_expectations_passed": not semantic_failed,
                "derived_artifact_provenance_passed": not (derived_artifact_failures or []),
                "domain_verdict_passed": not domain_verdict.get("enabled") or domain_verdict.get("status") == "pass",
            },
            "expected_artifacts": expected_artifacts,
            "missing_expected_artifacts": missing_expected_artifacts,
            "semantic_expectations": semantic_results,
            "semantic_expectations_passed": not semantic_failed,
            "schema_context": schema_context,
            "derived_artifact_failures": derived_artifact_failures or [],
            "artifact_provenance": artifact_provenance or {},
            "domain_verdict": domain_verdict,
        }
    }
    write_json(ai_dir / "artifact_verify_report.json", artifact)
    write_json(ai_dir / "schema_context.json", schema_context)
    write_json(ai_dir / "watchdog_report.json", {"watchdog_status": "healthy" if status == "SUCCESS" else "escalated", "last_action": "action-executor-complete" if status == "SUCCESS" else "WATCHDOG_ESCALATION_REQUIRED"})
    token_ledger = build_agent_token_ledger(run_dir)
    report = {
        "spec_id": "local-model-action-executor",
        "mode": "live-action-executor",
        "run_id": run_id,
        "run_dir": str(run_dir),
        "started_at": started_at,
        "finished_at": finished_at,
        "task": task,
        "error": error,
        "kpis": {
            "execution_jobs_run": 1,
            "accepted_count": 1 if status == "SUCCESS" else 0,
            "artifact_score": 1.0 if status == "SUCCESS" else 0.0,
            "artifact_pass_rate": 1.0 if status == "SUCCESS" else 0.0,
            "actual_changed_count": len(generated_files),
            "unsupported_action_count": 0,
            "attempt_count": attempt_count,
            "repair_rounds_used": repair_rounds_used,
            "expected_artifact_count": len(expected_artifacts),
            "missing_expected_artifact_count": len(missing_expected_artifacts),
            "seeded_file_count": len(seeded_files),
            "semantic_expectation_count": len(semantic_results),
            "semantic_expectation_passed_count": len(semantic_results) - len(semantic_failed),
            "semantic_expectation_failed_count": len(semantic_failed),
            "schema_context_available": schema_context.get("schema_context_available", False),
            "schema_context_source_count": schema_context.get("source_count", 0),
            "total_estimated_agent_tokens": token_ledger.get("total_estimated_agent_tokens", 0),
            "max_agent_turn_tokens": token_ledger.get("max_agent_turn_tokens", 0),
            "top_token_agent": token_ledger.get("top_token_agent", ""),
            "overflow_risk_agent_count": token_ledger.get("overflow_risk_agent_count", 0),
            "failure_category": failure_category,
            "execution_strategy": execution_strategy,
            "stage_count": stage_count,
            "transport_contract_failure_count": sum(1 for item in (transport_attempts or []) if item.get("contract_status") == "failed"),
            "derived_artifact_failure_count": len(derived_artifact_failures or []),
            "domain_verdict_enabled": bool(domain_verdict.get("enabled")),
            "domain_verdict_status": domain_verdict.get("status", "not_configured"),
            "domain_verdict_score": domain_verdict.get("score"),
            "domain_defect_count": len(domain_verdict.get("defects", [])),
        },
        "overall_status": "pass" if status == "SUCCESS" else "fail",
    }
    write_json(ai_dir / "task_harness_report.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a deterministic action manifest from a local model.")
    parser.add_argument("--task", default="")
    parser.add_argument("--task-file", default="")
    parser.add_argument("--manifest-file", default="")
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT))
    parser.add_argument("--run-id", default="")
    parser.add_argument("--max-actions", type=int, default=int(os.environ.get("LOCAL_ACTION_MAX_ACTIONS", "8")))
    parser.add_argument("--timeout-sec", type=int, default=int(os.environ.get("LOCAL_ACTION_MODEL_TIMEOUT_SEC", "180")))
    parser.add_argument("--allowed-commands", default=os.environ.get("LOCAL_ACTION_ALLOWED_COMMANDS", ",".join(sorted(DEFAULT_ALLOWED_COMMANDS))))
    parser.add_argument("--expected-artifact", action="append", default=[])
    parser.add_argument("--expect-json-value", action="append", default=[])
    parser.add_argument("--result-status", action="append", default=[], help="Runner-owned domain status as FILE:PATH")
    parser.add_argument("--result-pass-value", default="pass", help="JSON value accepted by --result-status")
    parser.add_argument("--compare-json", action="append", default=[], help="Deep compare worktree JSON as ACTUAL:EXPECTED")
    parser.add_argument("--require-json-path", action="append", default=[], help="Require worktree JSON path as FILE:PATH")
    parser.add_argument("--invariant", action="append", default=[], help="Restricted invariant as FILE:PATH<OP>JSON_VALUE")
    parser.add_argument("--seed-file", action="append", default=[])
    parser.add_argument("--schema-file", action="append", default=[])
    parser.add_argument("--max-repair-rounds", type=int, default=int(os.environ.get("LOCAL_ACTION_MAX_REPAIR_ROUNDS", "3")))
    parser.add_argument("--execution-strategy", choices=["auto", "single", "staged"], default=os.environ.get("LOCAL_ACTION_EXECUTION_STRATEGY", "auto"))
    parser.add_argument("--stage-artifact-limit", type=int, default=int(os.environ.get("LOCAL_ACTION_STAGE_ARTIFACT_LIMIT", "2")))
    parser.add_argument("--max-stages", type=int, default=int(os.environ.get("LOCAL_ACTION_MAX_STAGES", "4")))
    parser.add_argument("--derived-artifact", action="append", default=[])
    args = parser.parse_args()

    started_at = now_iso()
    task = args.task or (read_text(Path(args.task_file)) if args.task_file else "")
    if not task:
        print("task or task-file is required", file=sys.stderr)
        return 2
    run_id = args.run_id or f"run-{datetime.now().strftime('%Y%m%d-%H%M%S')}-local-action-executor"
    run_dir = Path(args.out_root).resolve() / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "task.txt").write_text(task, encoding="utf-8")
    allowed_commands = {item.strip() for item in args.allowed_commands.split(",") if item.strip()}
    seeded_files = seed_worktree(run_dir, args.seed_file)
    schema_context = build_schema_context(run_dir, seeded_files, args.schema_file)

    action_log: list[dict[str, Any]] = []
    generated_files: list[str] = []
    final_summary = ""
    error = ""
    missing_expected_artifacts: list[str] = []
    semantic_results: list[dict[str, Any]] = []
    status = "FAILED"
    failure_category = ""
    transport_attempts: list[dict[str, Any]] = []
    artifact_provenance: dict[str, dict[str, Any]] = {}
    derived_artifact_failures: list[str] = []
    domain_verdict: dict[str, Any] = {"enabled": False, "status": "not_configured", "score": None, "checks": [], "defects": [], "validator_source": "runner_owned"}
    attempt = 0
    repair_rounds_used = 0
    execution_strategy = args.execution_strategy
    if args.manifest_file:
        execution_strategy = "single"
    elif execution_strategy == "auto":
        execution_strategy = "staged" if len(args.expected_artifact) > 3 or args.max_actions > 8 else "single"
    stages = _artifact_batches(args.expected_artifact, max(1, args.stage_artifact_limit), args.max_stages) if execution_strategy == "staged" else [args.expected_artifact]

    for stage_number, stage_expected in enumerate(stages, start=1):
        repair_feedback = ""
        stage_succeeded = False
        stage_attempt_limit = 1 if args.manifest_file else max(1, args.max_repair_rounds + 1)
        for stage_attempt in range(1, stage_attempt_limit + 1):
            if stage_attempt > 1 and repair_rounds_used >= args.max_repair_rounds:
                break
            attempt += 1
            if stage_attempt > 1:
                repair_rounds_used += 1
            stage_task = task
            if execution_strategy == "staged":
                stage_task += (
                    f"\n\nBOUNDED STAGE {stage_number}/{len(stages)}. Produce only these artifacts in this stage: "
                    f"{stage_expected}. Already completed artifacts: {generated_files}. "
                    "Do not regenerate completed files. Keep this stage to at most three actions."
                )
            try:
                raw_manifest = read_text(Path(args.manifest_file)) if args.manifest_file else call_ccr_for_manifest(stage_task, args.timeout_sec, repair_feedback)
                transport_meta = {**_LAST_TRANSPORT_METADATA, "attempt": attempt, "stage": stage_number, "contract_status": "passed", "response_chars": len(raw_manifest)}
                transport_attempts.append(transport_meta)
                manifest_path = run_dir / "results" / f"job-001.stage-{stage_number}.attempt-{stage_attempt}.manifest.raw.txt"
                manifest_path.parent.mkdir(parents=True, exist_ok=True)
                manifest_path.write_text(raw_manifest, encoding="utf-8")
                if attempt == 1:
                    (run_dir / "results" / "job-001.manifest.raw.txt").write_text(raw_manifest, encoding="utf-8")
                manifest = parse_json_payload(raw_manifest)
                round_log, round_generated, stage_summary, round_provenance = execute_manifest(manifest, run_dir, allowed_commands, args.max_actions)
                for item in round_log:
                    item["stage"] = stage_number
                    item["stage_attempt"] = stage_attempt
                action_log.extend(round_log)
                generated_files = sorted(set([*generated_files, *round_generated]))
                artifact_provenance.update(round_provenance)
                final_summary = stage_summary or final_summary
                missing_stage = validate_expected_artifacts(run_dir, stage_expected)
                if missing_stage:
                    raise ActionExecutionError(f"missing stage artifacts: {', '.join(missing_stage)}", action_log, generated_files, final_summary)
                stage_derived = [item for item in args.derived_artifact if item in stage_expected]
                stage_provenance_failures = validate_derived_artifacts(stage_derived, artifact_provenance)
                if stage_provenance_failures:
                    derived_artifact_failures = stage_provenance_failures
                    failure_category = "artifact_provenance_failed"
                    generated_files = [item for item in generated_files if item not in stage_provenance_failures]
                    for item in stage_provenance_failures:
                        artifact_provenance.pop(item, None)
                        target = ensure_within(run_dir / "worktree", item)
                        if target.exists() and target.is_file():
                            target.unlink()
                    raise ActionExecutionError(
                        "derived artifacts in this stage lack run_command provenance: " + ", ".join(stage_provenance_failures),
                        action_log,
                        generated_files,
                        final_summary,
                    )
                if stage_number == len(stages):
                    semantic_results = validate_json_expectations(run_dir, args.expect_json_value)
                    semantic_failures = [item for item in semantic_results if item.get("status") == "failed"]
                    if semantic_failures:
                        raise ActionExecutionError("semantic expectations failed: " + json.dumps(semantic_failures, ensure_ascii=False), action_log, generated_files, final_summary)
                    derived_artifact_failures = validate_derived_artifacts(args.derived_artifact, artifact_provenance)
                    if derived_artifact_failures:
                        failure_category = "artifact_provenance_failed"
                        raise ActionExecutionError("derived artifacts lack run_command provenance: " + ", ".join(derived_artifact_failures), action_log, generated_files, final_summary)
                    domain_verdict = validate_domain_contract(
                        run_dir,
                        args.result_status,
                        parse_expected_value(args.result_pass_value),
                        args.compare_json,
                        args.require_json_path,
                        args.invariant,
                        args.schema_file,
                    )
                    if domain_verdict["enabled"] and domain_verdict["status"] != "pass":
                        failure_category = "domain_verdict_failed"
                        raise ActionExecutionError(
                            "runner-owned domain verdict failed: " + json.dumps(domain_verdict["defects"], ensure_ascii=False),
                            action_log,
                            generated_files,
                            final_summary,
                        )
                stage_succeeded = True
                derived_artifact_failures = []
                error = ""
                break
            except ManifestTransportError as exc:
                failure_category = exc.category
                error = str(exc)
                raw_path = run_dir / "results" / f"job-001.stage-{stage_number}.attempt-{stage_attempt}.transport.raw.txt"
                raw_path.parent.mkdir(parents=True, exist_ok=True)
                raw_path.write_text(exc.raw_response, encoding="utf-8")
                transport_attempts.append({**exc.metadata, "attempt": attempt, "stage": stage_number, "contract_status": "failed", "failure_category": exc.category, "raw_file": str(raw_path)})
            except ActionExecutionError as exc:
                if exc.action_log:
                    if len(exc.action_log) >= len(action_log):
                        action_log = exc.action_log
                    else:
                        action_log.extend(exc.action_log)
                generated_files = sorted(set([*generated_files, *exc.generated_files]))
                final_summary = exc.final_summary or final_summary
                error = str(exc)
            except Exception as exc:  # noqa: BLE001
                error = str(exc)
                if isinstance(exc, OSError) and getattr(exc, "winerror", None) == 10055:
                    failure_category = "transport_resource_exhausted"
                elif "WinError 10055" in error:
                    failure_category = "transport_resource_exhausted"
            missing_expected_artifacts = validate_expected_artifacts(run_dir, stage_expected)
            semantic_results = validate_json_expectations(run_dir, args.expect_json_value) if stage_number == len(stages) else []
            repair_feedback = build_repair_feedback(error, action_log[-8:], generated_files, stage_expected, missing_expected_artifacts, semantic_results, schema_context)
            if derived_artifact_failures:
                repair_feedback += (
                    "\nfailed_derived_artifacts: " + json.dumps(derived_artifact_failures) +
                    "\nrepair_instruction: Regenerate only these stage-owned derived artifacts through an allowlisted run_command. Do not rewrite completed artifacts from other stages."
                )
            if domain_verdict.get("defects"):
                repair_feedback += "\nrunner_owned_domain_defects: " + json.dumps(domain_verdict["defects"], ensure_ascii=False)[:2400]
            if stage_attempt < stage_attempt_limit and failure_category == "transport_resource_exhausted":
                time.sleep(min(2**stage_attempt, 8))
        if not stage_succeeded:
            break

    missing_expected_artifacts = validate_expected_artifacts(run_dir, args.expected_artifact)
    if not error and not missing_expected_artifacts and len(stages) > 0:
        generated_files = sorted(set([*generated_files, *args.expected_artifact]))
        status = "SUCCESS"
        failure_category = ""
    elif not failure_category:
        failure_category = "failed"
    report = build_artifacts(
        run_dir,
        run_id,
        task,
        status,
        action_log,
        generated_files,
        final_summary,
        error,
        attempt_count=attempt,
        repair_rounds_used=repair_rounds_used,
        expected_artifacts=args.expected_artifact,
        missing_expected_artifacts=missing_expected_artifacts,
        seeded_files=seeded_files,
        semantic_results=semantic_results,
        schema_context=schema_context,
        started_at=started_at,
        finished_at=now_iso(),
        failure_category=failure_category,
        transport_attempts=transport_attempts,
        artifact_provenance=artifact_provenance,
        derived_artifact_failures=derived_artifact_failures,
        domain_verdict=domain_verdict,
        execution_strategy=execution_strategy,
        stage_count=len(stages),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if status == "SUCCESS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
