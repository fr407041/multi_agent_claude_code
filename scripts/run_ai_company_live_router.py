from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = ROOT / "results" / "ai_company_task_harness"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def copy_scope(spec: dict[str, Any], run_dir: Path) -> Path:
    src = ROOT / str(spec.get("scope_copy_from", "tests/fixtures/ai_company_release_readiness_demo"))
    dst = run_dir / "worktree"
    shutil.copytree(src, dst)
    return dst


def apply_profile_defaults(job: dict[str, Any]) -> dict[str, Any]:
    role = str(job.get("owner_role") or "synthesis_agent")
    job.setdefault("agent_profile", role)
    job.setdefault("profile_mode", "strict")
    job.setdefault("effective_policy_level", "enforced-by-wrapper + verified-posthoc")
    job.setdefault("effective_tool_policy", "limited")
    return job


def worker_kind(job: dict[str, Any]) -> str:
    if str(job.get("worker_template", "")).strip().lower() == "summary_markdown":
        return "summary"
    if bool(job.get("require_change")) and len(job.get("files", [])) == 1:
        return "managed"
    return "generic"


def run_worker(job_path: Path, kind: str) -> int:
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "worker_claude_router.py"), str(job_path), kind],
        cwd=ROOT,
        check=False,
    ).returncode


def run_summary_repair(
    run_dir: Path,
    jobs_dir: Path,
    execution_log: list[dict[str, Any]],
    artifact: dict[str, Any],
) -> None:
    parsed = artifact.get("parsed") or {}
    checks = parsed.get("checks") or {}
    failed_checks = [name for name, ok in checks.items() if not ok]
    if not failed_checks:
        return
    job = {
        "id": "job-004-repair-001",
        "title": "Repair final release readiness summary",
        "owner_role": "synthesis_agent",
        "agent_profile": "synthesis_agent",
        "profile_mode": "strict",
        "effective_policy_level": "enforced-by-wrapper + verified-posthoc",
        "effective_tool_policy": "limited",
        "scope_path": str(run_dir / "worktree"),
        "files": ["summary.md"],
        "worker_template": "summary_markdown",
        "template_context_files": ["summary.md", "evidence_summary.txt", "summary_context.txt"],
        "require_change": True,
        "instruction": (
            "Repair summary.md so the verifier passes. Keep exactly 3 bullets plus 1 Takeaway sentence. "
            "Use only prepared evidence. Include the exact phrases required by failed checks. "
            f"Failed checks: {', '.join(failed_checks)}. "
            "Required exact phrases include: conditional go, evidence, rollback, uncertainty, router, overflow, "
            "42 unit, 11 passed, 4 of 5, 15 minutes, B1, B2. "
            "Stay under 170 words."
        ),
    }
    job_path = jobs_dir / f"{job['id']}.json"
    write_json(job_path, job)
    code = run_worker(job_path, "summary")
    execution_log.append({"task_id": job["id"], "owner_role": job.get("owner_role"), "exit_code": code, "repair": True})


def collect_statuses(results_dir: Path) -> list[dict[str, Any]]:
    statuses = []
    for path in sorted(results_dir.glob("*.status.json")):
        try:
            statuses.append(read_json(path))
        except json.JSONDecodeError:
            statuses.append({"id": path.stem.replace(".status", ""), "status": "FAILED", "verification_note": "invalid status json"})
    return statuses


def build_claim_ledger(run_id: str, statuses: list[dict[str, Any]]) -> dict[str, Any]:
    claims = []
    for status in statuses:
        for claim in status.get("key_claims", []) or []:
            claims.append(
                {
                    "task_id": status.get("id"),
                    "agent_profile": status.get("agent_profile"),
                    "claim": claim.get("claim", ""),
                    "evidence_refs": claim.get("evidence_refs", []),
                    "confidence": status.get("confidence", "low"),
                }
            )
    claim_count = len(claims)
    with_evidence = sum(1 for item in claims if item.get("evidence_refs"))
    return {
        "run_id": run_id,
        "claims": claims,
        "metrics": {
            "claim_count": claim_count,
            "claim_coverage_rate": round(with_evidence / claim_count, 3) if claim_count else 0.0,
            "uncertainty_gap_count": 0,
        },
    }


def failure_family_counts(statuses: list[dict[str, Any]], reviewer: dict[str, Any]) -> dict[str, int]:
    counts = {
        "pseudo_tool_call": 0,
        "missing_expected_artifact": 0,
        "router": 0,
        "overflow": 0,
        "timeout": 0,
        "failed": 0,
        "repair_required": 0,
    }
    for status in statuses:
        if status.get("failure_family") == "pseudo_tool_call" or status.get("status") == "PSEUDO_TOOL_CALL_DETECTED":
            counts["pseudo_tool_call"] += 1
        if status.get("require_change") and not status.get("actual_changed_files"):
            counts["missing_expected_artifact"] += 1
        if status.get("status") == "ROUTER_ERROR":
            counts["router"] += 1
        if status.get("status") == "OVERFLOW_DETECTED":
            counts["overflow"] += 1
        if status.get("status") == "CHILD_TIMEOUT":
            counts["timeout"] += 1
        if status.get("status") == "FAILED":
            counts["failed"] += 1
    counts["repair_required"] = sum(1 for item in reviewer.get("verdicts", []) if item.get("verdict") == "REPAIR_REQUIRED")
    return counts


def build_reviewer(run_id: str, statuses: list[dict[str, Any]], claim_ledger: dict[str, Any]) -> dict[str, Any]:
    verdicts = []
    for status in statuses:
        ok = status.get("status") == "SUCCESS" and bool(status.get("key_claims"))
        verdicts.append(
            {
                "task_id": status.get("id"),
                "agent_profile": status.get("agent_profile"),
                "verdict": "ACCEPTED" if ok else "REPAIR_REQUIRED",
                "profile_policy_status": "ok" if status.get("agent_profile") else "PROFILE_POLICY_VIOLATION",
                "profile_policy_notes": status.get("verification_note", ""),
                "evidence": status.get("key_claims", []),
                "repair_action": "" if ok else "Review raw output and rerun with narrower scope.",
            }
        )
    accepted = sum(1 for item in verdicts if item["verdict"] == "ACCEPTED")
    return {
        "run_id": run_id,
        "verdict_count": len(verdicts),
        "accepted_count": accepted,
        "false_success_blocked_count": 0,
        "profile_policy_violation_count": sum(1 for item in verdicts if item["profile_policy_status"] != "ok"),
        "claim_ledger_metrics": claim_ledger.get("metrics", {}),
        "claim_contract": {
            "accepted_tasks_have_claim": all(bool(status.get("key_claims")) for status in statuses if status.get("status") == "SUCCESS"),
            "all_claims_have_evidence": all(bool(item.get("evidence_refs")) for item in claim_ledger.get("claims", [])),
        },
        "verdicts": verdicts,
    }


def verify_summary(run_dir: Path) -> dict[str, Any]:
    summary_path = run_dir / "worktree" / "summary.md"
    command = [sys.executable, str(ROOT / "scripts" / "verify_common_research_artifact.py"), str(run_dir / "worktree")]
    proc = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    parsed: dict[str, Any] | None = None
    if proc.stdout.strip():
        try:
            parsed = json.loads(proc.stdout)
        except json.JSONDecodeError:
            parsed = None
    if parsed is None:
        parsed = {
            "summary_file": str(summary_path),
            "score": 0.0,
            "all_passed": False,
            "checks": {"summary_file_exists": summary_path.exists(), "verify_json_parse": False},
        }
    return {"command": command, "stdout": proc.stdout, "stderr": proc.stderr, "parsed": parsed}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the public live router demo through CCR.")
    parser.add_argument("spec")
    parser.add_argument("--out-root", default=str(OUTPUT_ROOT))
    args = parser.parse_args()

    spec_path = Path(args.spec).resolve()
    spec = read_json(spec_path)
    run_id = f"run-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{spec.get('id', 'live-router')}"
    run_dir = Path(args.out_root).resolve() / run_id
    ai_dir = run_dir / "ai_company"
    jobs_dir = run_dir / "jobs"
    results_dir = run_dir / "results"
    ai_dir.mkdir(parents=True, exist_ok=True)
    jobs_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)
    worktree = copy_scope(spec, run_dir)

    assignments = []
    for source in spec.get("jobs", []):
        job = apply_profile_defaults(dict(source))
        job["scope_path"] = str(worktree)
        job_path = jobs_dir / f"{job['id']}.json"
        write_json(job_path, job)
        assignments.append(
            {
                "task_id": job["id"],
                "owner_role": job.get("owner_role"),
                "agent_profile": job.get("agent_profile"),
                "profile_mode": job.get("profile_mode"),
                "state": "running",
            }
        )

    meeting = {
        "run_id": run_id,
        "meeting_status": "MEETING_READY",
        "rounds_used": 1,
        "run_profile_mode": spec.get("agent_profile_mode", "strict"),
        "task_assignments": assignments,
        "discussion_log": ["public live runner materialized strict task assignments"],
    }
    write_json(ai_dir / "meeting_decision.json", meeting)

    execution_log = []
    for job_path in sorted(jobs_dir.glob("job-*.json")):
        job = read_json(job_path)
        code = run_worker(job_path, worker_kind(job))
        execution_log.append({"task_id": job["id"], "owner_role": job.get("owner_role"), "exit_code": code})

    statuses = collect_statuses(results_dir)
    claim_ledger = build_claim_ledger(run_id, statuses)
    reviewer = build_reviewer(run_id, statuses, claim_ledger)
    families = failure_family_counts(statuses, reviewer)
    artifact = verify_summary(run_dir)
    repair_attempted = False
    if not bool((artifact.get("parsed") or {}).get("all_passed", False)):
        run_summary_repair(run_dir, jobs_dir, execution_log, artifact)
        repair_attempted = True
        statuses = collect_statuses(results_dir)
        claim_ledger = build_claim_ledger(run_id, statuses)
        reviewer = build_reviewer(run_id, statuses, claim_ledger)
        families = failure_family_counts(statuses, reviewer)
        artifact = verify_summary(run_dir)
    accepted_count = int(reviewer.get("accepted_count", 0))
    all_status_success = all(item.get("status") == "SUCCESS" for item in statuses) and len(statuses) >= len(spec.get("jobs", []))
    artifact_passed = bool((artifact.get("parsed") or {}).get("all_passed", False))
    overall_status = "pass" if all_status_success and accepted_count == len(statuses) and artifact_passed else "fail"

    write_json(ai_dir / "subagent_claim_ledger.json", claim_ledger)
    write_json(ai_dir / "reviewer_verdicts.json", reviewer)
    write_json(ai_dir / "artifact_verify_report.json", artifact)
    write_json(ai_dir / "watchdog_report.json", {"watchdog_status": "healthy" if overall_status == "pass" else "escalated", "last_action": "live-run-complete", "failure_family_counts": families})
    write_json(ai_dir / "main_agent_memory_guard_report.json", {"memory_guard_enabled": True, "memory_checkpoint_count": 0, "checkpoint_decision": "not_evaluated_in_public_live_runner"})
    write_json(ai_dir / "execution_summary.json", {"execution_jobs_run": len(execution_log), "execution_log": execution_log})
    report = {
        "spec_id": spec.get("id"),
        "mode": "live",
        "run_id": run_id,
        "run_dir": str(run_dir),
        "kpis": {
            "execution_jobs_run": len(execution_log),
            "accepted_count": accepted_count,
            "reviewer_verdict_count": len(reviewer.get("verdicts", [])),
            "claim_count": claim_ledger["metrics"]["claim_count"],
            "claim_coverage_rate": claim_ledger["metrics"]["claim_coverage_rate"],
            "artifact_score": (artifact.get("parsed") or {}).get("score", 0.0),
            "artifact_pass_rate": 1.0 if artifact_passed else 0.0,
            "run_profile_mode": spec.get("agent_profile_mode", "strict"),
            "profile_attachment_rate": 1.0 if statuses and all(item.get("agent_profile") for item in statuses) else 0.0,
            "profile_policy_violation_count": reviewer.get("profile_policy_violation_count", 0),
            "repair_attempted": repair_attempted,
            "failure_family_counts": families,
            "pseudo_tool_call_count": families["pseudo_tool_call"],
            "missing_expected_artifact_count": families["missing_expected_artifact"],
        },
        "overall_status": overall_status,
    }
    write_json(ai_dir / "task_harness_report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if overall_status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
