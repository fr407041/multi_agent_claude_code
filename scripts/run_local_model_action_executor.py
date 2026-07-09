#!/usr/bin/env python3
"""Deterministic action executor for local-model live tasks.

The local model is responsible for proposing the actions. This runner only
validates and executes a small allowlisted action manifest, then emits the same
artifact shape the dashboard/watchdog already know how to read.
"""

from __future__ import annotations

import argparse
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


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_ROOT = ROOT / "results" / "ai_company_task_harness"
ALLOWED_ACTIONS = {"write_file", "read_file", "run_command", "finish"}
DEFAULT_ALLOWED_COMMANDS = {"python3", "python", "curl", "sed", "awk", "grep", "find", "mkdir", "cat", "head", "tail"}


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


def call_ccr_for_manifest(task: str, timeout_sec: int, repair_feedback: str = "") -> str:
    url = os.environ.get("CCR_MESSAGES_URL", "http://127.0.0.1:3456/v1/messages")
    model = os.environ.get("CLAUDE_MODEL_ALIAS", "ollama,qwen2.5-coder:14b")
    api_key = os.environ.get("CCR_API_KEY", os.environ.get("ANTHROPIC_API_KEY", "local-router-token"))
    max_tokens = int(os.environ.get("CCR_MAX_OUTPUT_TOKENS", "2048"))
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
) -> tuple[list[dict[str, Any]], list[str], str]:
    worktree = run_dir / "worktree"
    worktree.mkdir(parents=True, exist_ok=True)
    actions = manifest.get("actions")
    if not isinstance(actions, list):
        raise ValueError("action manifest must contain actions: []")
    if len(actions) > max_actions:
        raise ValueError(f"too many actions: {len(actions)} > {max_actions}")
    action_log: list[dict[str, Any]] = []
    generated_files: list[str] = []
    final_summary = ""
    for index, action in enumerate(actions, start=1):
        if not isinstance(action, dict):
            raise ValueError(f"action {index} must be an object")
        action_type = str(action.get("type") or "")
        if action_type not in ALLOWED_ACTIONS:
            raise ValueError(f"unsupported action type: {action_type}")
        entry: dict[str, Any] = {"index": index, "type": action_type, "started_at": now_iso()}
        if action_type == "write_file":
            target = ensure_within(worktree, str(action.get("path") or ""))
            content = str(action.get("content") or "")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            rel = str(target.relative_to(worktree).as_posix())
            generated_files.append(rel)
            entry.update({"path": rel, "bytes": len(content.encode("utf-8")), "status": "ok"})
        elif action_type == "read_file":
            target = ensure_within(worktree, str(action.get("path") or ""))
            if not target.exists() or not target.is_file():
                raise ValueError(f"read_file target missing: {action.get('path')}")
            entry.update({"path": str(target.relative_to(worktree).as_posix()), "chars": len(read_text(target)), "status": "ok"})
        elif action_type == "run_command":
            command = action.get("command")
            if not isinstance(command, list) or not command or not all(isinstance(item, str) for item in command):
                raise ValueError("run_command.command must be a non-empty string array")
            executable = Path(command[0]).name
            if executable not in allowed_commands:
                raise ValueError(f"command not allowlisted: {command[0]}")
            timeout_sec = int(action.get("timeout_sec") or 60)
            proc = subprocess.run(command, cwd=worktree, text=True, capture_output=True, timeout=timeout_sec, check=False)
            entry.update(
                {
                    "command": command,
                    "timeout_sec": timeout_sec,
                    "return_code": proc.returncode,
                    "stdout": proc.stdout[-4000:],
                    "stderr": proc.stderr[-4000:],
                    "status": "ok" if proc.returncode == 0 else "failed",
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
    return action_log, sorted(set(generated_files)), final_summary


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
) -> dict[str, Any]:
    ai_dir = run_dir / "ai_company"
    results_dir = run_dir / "results"
    ai_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)
    raw_file = results_dir / "job-001.raw.txt"
    raw_file.write_text(final_summary or error or json.dumps(action_log, ensure_ascii=False, indent=2), encoding="utf-8")
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
        "failure_family": "" if status == "SUCCESS" else "failed",
        "verification_note": "deterministic action manifest executed" if status == "SUCCESS" else error,
        "raw_file": str(raw_file),
        "exec_log_file": str(results_dir / "job-001.exec.log"),
        "exit_code": 0 if status == "SUCCESS" else 1,
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
            },
            "expected_artifacts": expected_artifacts,
            "missing_expected_artifacts": missing_expected_artifacts,
            "semantic_expectations": semantic_results,
            "semantic_expectations_passed": not semantic_failed,
            "schema_context": schema_context,
        }
    }
    write_json(ai_dir / "artifact_verify_report.json", artifact)
    write_json(ai_dir / "schema_context.json", schema_context)
    write_json(ai_dir / "watchdog_report.json", {"watchdog_status": "healthy" if status == "SUCCESS" else "escalated", "last_action": "action-executor-complete" if status == "SUCCESS" else "WATCHDOG_ESCALATION_REQUIRED"})
    report = {
        "spec_id": "local-model-action-executor",
        "mode": "live-action-executor",
        "run_id": run_id,
        "run_dir": str(run_dir),
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
    parser.add_argument("--seed-file", action="append", default=[])
    parser.add_argument("--schema-file", action="append", default=[])
    parser.add_argument("--max-repair-rounds", type=int, default=int(os.environ.get("LOCAL_ACTION_MAX_REPAIR_ROUNDS", "3")))
    args = parser.parse_args()

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
    repair_feedback = ""
    max_attempts = 1 if args.manifest_file else max(1, args.max_repair_rounds + 1)
    for attempt in range(1, max_attempts + 1):
        try:
            raw_manifest = read_text(Path(args.manifest_file)) if args.manifest_file else call_ccr_for_manifest(task, args.timeout_sec, repair_feedback)
            manifest_path = run_dir / "results" / f"job-001.manifest.round-{attempt}.raw.txt"
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text(raw_manifest, encoding="utf-8")
            if attempt == 1:
                (run_dir / "results" / "job-001.manifest.raw.txt").write_text(raw_manifest, encoding="utf-8")
            manifest = parse_json_payload(raw_manifest)
            round_log, round_generated, final_summary = execute_manifest(manifest, run_dir, allowed_commands, args.max_actions)
            action_log.extend(round_log)
            generated_files = sorted(set([*generated_files, *round_generated]))
            missing_expected_artifacts = validate_expected_artifacts(run_dir, args.expected_artifact)
            if missing_expected_artifacts:
                raise ActionExecutionError(
                    f"missing expected artifacts: {', '.join(missing_expected_artifacts)}",
                    action_log,
                    generated_files,
                    final_summary,
                )
            semantic_results = validate_json_expectations(run_dir, args.expect_json_value)
            semantic_failures = [item for item in semantic_results if item.get("status") == "failed"]
            if semantic_failures:
                raise ActionExecutionError(
                    "semantic expectations failed: " + json.dumps(semantic_failures, ensure_ascii=False),
                    action_log,
                    generated_files,
                    final_summary,
                )
            generated_files = sorted(set([*generated_files, *args.expected_artifact]))
            status = "SUCCESS"
            error = ""
            break
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
        missing_expected_artifacts = validate_expected_artifacts(run_dir, args.expected_artifact)
        semantic_results = validate_json_expectations(run_dir, args.expect_json_value)
        repair_feedback = build_repair_feedback(
            error,
            action_log,
            generated_files,
            args.expected_artifact,
            missing_expected_artifacts,
            semantic_results,
            schema_context,
        )
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
        repair_rounds_used=max(0, attempt - 1),
        expected_artifacts=args.expected_artifact,
        missing_expected_artifacts=missing_expected_artifacts,
        seeded_files=seeded_files,
        semantic_results=semantic_results,
        schema_context=schema_context,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if status == "SUCCESS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
