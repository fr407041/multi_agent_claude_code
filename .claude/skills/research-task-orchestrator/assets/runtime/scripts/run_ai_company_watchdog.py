from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from materialize_ai_company_task_run import ROOT
from run_ai_company_execution import ensure_status_file
from agent_profile_resolver import build_profile_summary
from subagent_claim_ledger import validate_claim_contract, write_claim_ledger


DEFAULTS_PATH = ROOT / "configs" / "ai_company" / "task_harness.defaults.json"
RESULTS_ROOT = ROOT / "results" / "ai_company_task_harness"
REPORT_NAME = "watchdog_report.json"
EVENTS_NAME = "watchdog_events.jsonl"
LOCK_NAME = "watchdog_lock.json"
HEARTBEAT_NAME = "heartbeat.json"
TERMINAL_STATUSES = {"SUCCESS", "FAILED", "OVERFLOW_DETECTED", "ROUTER_ERROR", "CHILD_TIMEOUT", "CHILD_LIMIT_REACHED", "NEEDS_REPLAN", "PROFILE_POLICY_VIOLATION", "PROFILE_DOWNGRADED", "PROFILE_ENFORCEMENT_UNAVAILABLE"}
NON_TERMINAL_STATUSES = {"QUEUED", "PENDING", "RUNNING", "IN_PROGRESS", "REVIEW_PENDING"}
VALID_MEETING_STATUSES = {"MEETING_READY", "MEETING_NEEDS_REPLAN"}


def read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except (json.JSONDecodeError, OSError):
        return {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def append_event(run_dir: Path, event: dict[str, Any]) -> None:
    events_path = run_dir / "ai_company" / EVENTS_NAME
    events_path.parent.mkdir(parents=True, exist_ok=True)
    with events_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat()


def path_age_sec(path: Path) -> float:
    if not path.exists():
        return float("inf")
    return max(0.0, time.time() - path.stat().st_mtime)


def load_defaults() -> dict[str, Any]:
    return read_json(DEFAULTS_PATH)


def latest_run_dir(results_root: Path = RESULTS_ROOT) -> Path:
    candidates = sorted([path for path in results_root.glob("run-*") if path.is_dir()], key=lambda item: item.name, reverse=True)
    if not candidates:
        raise SystemExit(f"No ai-company runs found under {results_root}")
    return candidates[0]


def non_review_jobs(run_dir: Path) -> list[dict[str, Any]]:
    jobs = []
    for path in sorted((run_dir / "jobs").glob("*.json")):
        job = read_json(path)
        if job.get("owner_role") == "reviewer_worker":
            continue
        job["_job_path"] = str(path)
        jobs.append(job)
    return jobs


def status_path_for(run_dir: Path, task_id: str) -> Path:
    return run_dir / "results" / f"{task_id}.status.json"


def status_records(run_dir: Path) -> list[dict[str, Any]]:
    rows = []
    for path in sorted((run_dir / "results").glob("*.status.json")):
        payload = read_json(path)
        payload["_status_path"] = str(path)
        rows.append(payload)
    return rows


def write_heartbeat(run_dir: Path, phase: str, state: str = "running") -> dict[str, Any]:
    heartbeat = {
        "run_id": run_dir.name,
        "phase": phase,
        "state": state,
        "updated_at": iso_now(),
    }
    try:
        write_json(run_dir / "ai_company" / HEARTBEAT_NAME, heartbeat)
    except PermissionError:
        heartbeat["write_error"] = "permission_denied"
    return heartbeat


def render_template_command(command: str, run_dir: Path) -> list[str]:
    scope_path = (run_dir / "worktree") if (run_dir / "worktree").exists() else run_dir
    rendered = (
        str(command)
        .replace("{root}", ROOT.as_posix())
        .replace("{run_dir}", run_dir.as_posix())
        .replace("{scope_path}", scope_path.as_posix())
    )
    argv = shlex.split(rendered, posix=True)
    if argv and argv[0] == "python3":
        argv[0] = sys.executable
    return argv


def run_reviewer(run_dir: Path) -> dict[str, Any]:
    out_file = run_dir / "ai_company" / "reviewer_verdicts.json"
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "run_ai_company_reviewer_worker.py"), str(run_dir), str(out_file)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return read_json(out_file)


def run_post_verify(run_dir: Path) -> dict[str, Any] | None:
    report = read_json(run_dir / "ai_company" / "task_harness_report.json")
    spec_path = Path(str(report.get("spec_file", "")))
    if not spec_path.exists():
        return None
    spec = read_json(spec_path)
    command = spec.get("post_verify_command")
    if not command:
        return None
    argv = render_template_command(str(command), run_dir)
    proc = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, check=False)
    parsed = None
    if proc.stdout.strip():
        try:
            parsed = json.loads(proc.stdout.strip())
        except json.JSONDecodeError:
            parsed = None
    payload: dict[str, Any] = {
        "command": argv,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "exit_code": proc.returncode,
        "detected_by": "watchdog",
    }
    if parsed is not None:
        payload["parsed"] = parsed
    write_json(run_dir / "ai_company" / "artifact_verify_report.json", payload)
    return payload


def latest_artifact_mtime(run_dir: Path) -> float:
    latest = 0.0
    for folder in [run_dir / "ai_company", run_dir / "results"]:
        if not folder.exists():
            continue
        for path in folder.rglob("*"):
            if path.is_file() and path.name != LOCK_NAME:
                latest = max(latest, path.stat().st_mtime)
    return latest


def acquire_lock(run_dir: Path) -> Path | None:
    lock_path = run_dir / "ai_company" / LOCK_NAME
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return None
    except PermissionError:
        return lock_path.with_name("__watchdog_lock_unavailable__")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps({"pid": "watchdog", "created_at": iso_now()}, ensure_ascii=False))
    return lock_path


def event(event_type: str, severity: str, action: str, detail: str, task_id: str = "") -> dict[str, Any]:
    return {
        "created_at": iso_now(),
        "type": event_type,
        "severity": severity,
        "task_id": task_id,
        "action": action,
        "detail": detail,
        "detected_by": "watchdog",
    }


def repair_missing_status(run_dir: Path, job: dict[str, Any]) -> dict[str, Any]:
    status_path = status_path_for(run_dir, str(job.get("id", "")))
    ensure_status_file(job, status_path)
    status = read_json(status_path)
    status["detected_by"] = "watchdog"
    status["failure_reason"] = "MISSING_STATUS_FILE"
    status["recommended_next_action"] = "Review fallback failure status and replan with narrower scope if needed."
    write_json(status_path, status)
    return event(
        "MISSING_STATUS_FILE",
        "warning",
        "created_fallback_status",
        f"Created fallback status file {status_path.name}.",
        str(job.get("id", "")),
    )


def repair_stale_status(run_dir: Path, status: dict[str, Any]) -> dict[str, Any]:
    status_path = Path(str(status.get("_status_path", "")))
    repaired = dict(status)
    repaired.pop("_status_path", None)
    repaired["status"] = "CHILD_TIMEOUT"
    repaired["detected_by"] = "watchdog"
    repaired["failure_reason"] = "STALE_RUNNING_TASK"
    repaired["verification_note"] = "Watchdog marked this task timed out because it stayed non-terminal past the stale threshold."
    repaired["recommended_next_action"] = "Replan this task with smaller scope; do not retry the same broad job unchanged."
    write_json(status_path, repaired)
    return event(
        "STALE_RUNNING_TASK",
        "warning",
        "marked_child_timeout",
        f"Marked stale non-terminal task {repaired.get('id', '')} as CHILD_TIMEOUT.",
        str(repaired.get("id", "")),
    )


def update_report(run_dir: Path, report: dict[str, Any]) -> None:
    write_json(run_dir / "ai_company" / REPORT_NAME, report)


def watchdog_once(run_dir: Path, defaults: dict[str, Any] | None = None) -> dict[str, Any]:
    defaults = defaults or load_defaults()
    ai_dir = run_dir / "ai_company"
    ai_dir.mkdir(parents=True, exist_ok=True)

    if not bool(defaults.get("watchdog_enabled", True)):
        report = {
            "run_id": run_dir.name,
            "watchdog_status": "disabled",
            "last_check_at": iso_now(),
            "events": [],
            "last_action": "disabled",
        }
        update_report(run_dir, report)
        return report

    lock_path = acquire_lock(run_dir)
    if lock_path is None:
        return {
            "run_id": run_dir.name,
            "watchdog_status": "skipped",
            "last_check_at": iso_now(),
            "events": [],
            "last_action": "lock_exists",
        }

    events: list[dict[str, Any]] = []
    if lock_path.name == "__watchdog_lock_unavailable__":
        events.append(event("WATCHDOG_LOCK_UNAVAILABLE", "warning", "continue_without_lock", "Could not create watchdog lock; continuing a single bounded check."))
    actions_used = 0
    max_actions = int(defaults.get("watchdog_max_total_actions_per_run", 3))
    stale_after = int(defaults.get("watchdog_stale_after_sec", 180))
    try:
        write_heartbeat(run_dir, "watchdog_check", "running")
        meeting = read_json(ai_dir / "meeting_decision.json")
        if meeting.get("meeting_status") not in VALID_MEETING_STATUSES:
            events.append(event("MEETING_MISSING_OR_INVALID", "error", "escalate", "Meeting decision is missing or has an invalid status."))

        jobs = non_review_jobs(run_dir)
        statuses = {str(item.get("id")): item for item in status_records(run_dir)}
        for job in jobs:
            task_id = str(job.get("id", ""))
            status = statuses.get(task_id)
            if not status and actions_used < max_actions:
                evt = repair_missing_status(run_dir, job)
                events.append(evt)
                append_event(run_dir, evt)
                actions_used += 1
            elif not status:
                events.append(event("MISSING_STATUS_FILE", "error", "budget_exhausted", "Status file missing but repair budget is exhausted.", task_id))

        statuses = {str(item.get("id")): item for item in status_records(run_dir)}
        for status in statuses.values():
            raw_status = str(status.get("status", "")).upper()
            status_path = Path(str(status.get("_status_path", "")))
            if raw_status in NON_TERMINAL_STATUSES and path_age_sec(status_path) > stale_after:
                if actions_used < max_actions:
                    evt = repair_stale_status(run_dir, status)
                    events.append(evt)
                    append_event(run_dir, evt)
                    actions_used += 1
                else:
                    events.append(event("STALE_RUNNING_TASK", "error", "budget_exhausted", "Task is stale but repair budget is exhausted.", str(status.get("id", ""))))

        reviewer_path = ai_dir / "reviewer_verdicts.json"
        reviewer = read_json(reviewer_path)
        status_ids = {str(item.get("id")) for item in status_records(run_dir)}
        verdict_ids = {str(item.get("task_id")) for item in reviewer.get("verdicts", [])}
        if (not reviewer_path.exists() or not status_ids.issubset(verdict_ids)) and actions_used < max_actions:
            run_reviewer(run_dir)
            evt = event("REVIEWER_MISSING", "warning", "reran_reviewer", "Reviewer verdicts were missing or incomplete and were regenerated.")
            events.append(evt)
            append_event(run_dir, evt)
            actions_used += 1

        ledger = write_claim_ledger(run_dir)
        profile_summary = build_profile_summary(run_dir)
        profile_violations = profile_summary.get("policy_violations", [])
        for violation in profile_violations:
            evt = event(
                "PROFILE_POLICY_VIOLATION",
                "error",
                "WATCHDOG_ESCALATION_REQUIRED",
                f"Agent profile policy issue: {violation.get('issue', '')}.",
                str(violation.get("task_id", "")),
            )
            events.append(evt)
            append_event(run_dir, evt)
        reviewer = read_json(reviewer_path)
        accepted_ids = {str(item.get("task_id")) for item in reviewer.get("verdicts", []) if item.get("verdict") == "ACCEPTED"}
        claim_contract = validate_claim_contract(ledger, accepted_ids)
        if not (claim_contract.get("all_claims_have_evidence") and claim_contract.get("accepted_tasks_have_claim")):
            evt = event("CLAIM_LEDGER_MISSING_OR_INVALID", "error", "rebuilt_claim_ledger", "Claim ledger contract is not satisfied after rebuild.")
            events.append(evt)
            append_event(run_dir, evt)

        artifact_path = ai_dir / "artifact_verify_report.json"
        artifact = read_json(artifact_path)
        artifact_parsed = artifact.get("parsed", {}) if isinstance(artifact, dict) else {}
        if bool(defaults.get("watchdog_require_final_artifact_verify", True)) and (not artifact_path.exists() or not artifact_parsed.get("all_passed", False)):
            repaired_artifact = None
            if actions_used < max_actions:
                repaired_artifact = run_post_verify(run_dir)
                actions_used += 1 if repaired_artifact is not None else 0
            repaired_parsed = (repaired_artifact or {}).get("parsed", {}) if isinstance(repaired_artifact, dict) else {}
            if repaired_artifact is not None and repaired_parsed.get("all_passed", False):
                evt = event("POST_VERIFY_MISSING", "warning", "reran_post_verify", "Post verify was regenerated and now passes.")
            else:
                evt = event("FINAL_NOT_PASS", "error", "WATCHDOG_ESCALATION_REQUIRED", "Final artifact verify is missing or failing; bounded automatic recovery is exhausted or insufficient.")
            events.append(evt)
            append_event(run_dir, evt)

        latest_mtime = latest_artifact_mtime(run_dir)
        stale_run = latest_mtime > 0 and (time.time() - latest_mtime) > stale_after
        if stale_run:
            events.append(event("RUN_STALE", "warning", "observe", "Run has no recent artifact updates."))

        stale_task_count = sum(1 for item in status_records(run_dir) if str(item.get("status", "")).upper() in NON_TERMINAL_STATUSES)
        escalated = any(item.get("action") == "WATCHDOG_ESCALATION_REQUIRED" or item.get("severity") == "error" for item in events)
        report = {
            "run_id": run_dir.name,
            "watchdog_status": "escalated" if escalated else "repairing" if actions_used else "healthy",
            "last_check_at": iso_now(),
            "last_action": events[-1]["action"] if events else "none",
            "stale_task_count": stale_task_count,
            "repair_attempts_used": actions_used,
            "max_total_actions_per_run": max_actions,
            "events": events,
            "profile_policy_violations": profile_violations,
            "profile_downgrade_events": profile_summary.get("downgrade_summary", []),
            "kpis": {
                "watchdog_detection_rate": 1.0 if events else 0.0,
                "watchdog_recovery_rate": round(actions_used / len(events), 3) if events else 1.0,
                "stale_task_count": stale_task_count,
                "silent_failure_block_rate": 1.0 if events else 0.0,
                "mean_time_to_detection_sec": stale_after,
            },
        }
        write_heartbeat(run_dir, "watchdog_check", "complete")
        update_report(run_dir, report)
        return report
    finally:
        if lock_path.name != "__watchdog_lock_unavailable__":
            try:
                lock_path.unlink()
            except OSError:
                pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Run ai-company watchdog checks for one run or the latest run.")
    parser.add_argument("run_dir", nargs="?", help="Run directory. Defaults to latest run under results/ai_company_task_harness.")
    parser.add_argument("--watch", action="store_true", help="Keep checking at watchdog_interval_sec.")
    parser.add_argument("--once", action="store_true", help="Run a single check and exit.")
    args = parser.parse_args()

    defaults = load_defaults()
    run_dir = Path(args.run_dir).resolve() if args.run_dir else latest_run_dir()
    interval = int(defaults.get("watchdog_interval_sec", 30))

    while True:
        report = watchdog_once(run_dir, defaults)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if args.once or not args.watch:
            break
        time.sleep(interval)


if __name__ == "__main__":
    main()
