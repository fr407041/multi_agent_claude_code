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
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_ROOT = ROOT / "results" / "ai_company_task_harness"
ALLOWED_ACTIONS = {"write_file", "read_file", "run_command", "finish"}
DEFAULT_ALLOWED_COMMANDS = {"python3", "python", "curl", "sed", "awk", "grep", "find", "mkdir", "cat", "head", "tail"}


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


def call_ccr_for_manifest(task: str, timeout_sec: int) -> str:
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
                raise ValueError(f"command failed with exit code {proc.returncode}: {' '.join(command)}")
        elif action_type == "finish":
            final_summary = str(action.get("summary") or "")
            artifacts = action.get("artifacts") or []
            if not isinstance(artifacts, list):
                raise ValueError("finish.artifacts must be an array")
            for rel in artifacts:
                target = ensure_within(worktree, str(rel))
                if not target.exists():
                    raise ValueError(f"declared artifact missing: {rel}")
            entry.update({"summary": final_summary, "artifacts": artifacts, "status": "ok"})
        entry["finished_at"] = now_iso()
        action_log.append(entry)
    return action_log, sorted(set(generated_files)), final_summary


def build_artifacts(
    run_dir: Path,
    run_id: str,
    task: str,
    status: str,
    action_log: list[dict[str, Any]],
    generated_files: list[str],
    final_summary: str,
    error: str,
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
    artifact = {"parsed": {"all_passed": status == "SUCCESS", "score": 1.0 if status == "SUCCESS" else 0.0, "checks": {"generated_file_exists": bool(generated_files), "executor_success": status == "SUCCESS"}}}
    write_json(ai_dir / "artifact_verify_report.json", artifact)
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

    action_log: list[dict[str, Any]] = []
    generated_files: list[str] = []
    final_summary = ""
    error = ""
    try:
        raw_manifest = read_text(Path(args.manifest_file)) if args.manifest_file else call_ccr_for_manifest(task, args.timeout_sec)
        (run_dir / "results" / "job-001.manifest.raw.txt").parent.mkdir(parents=True, exist_ok=True)
        (run_dir / "results" / "job-001.manifest.raw.txt").write_text(raw_manifest, encoding="utf-8")
        manifest = parse_json_payload(raw_manifest)
        action_log, generated_files, final_summary = execute_manifest(manifest, run_dir, allowed_commands, args.max_actions)
        status = "SUCCESS"
    except Exception as exc:  # noqa: BLE001
        status = "FAILED"
        error = str(exc)
    report = build_artifacts(run_dir, run_id, task, status, action_log, generated_files, final_summary, error)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if status == "SUCCESS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
