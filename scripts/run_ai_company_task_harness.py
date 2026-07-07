#!/usr/bin/env python3
"""Standalone mock AI-company harness for the first-run demo.

This file intentionally has no third-party dependencies. It proves the checkout
can create the expected orchestration artifacts before users configure live
Claude Code Router or a local model endpoint.
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SUMMARY = "\n".join([
    "- Conditional go is supported by evidence from 42 unit tests, 11 passed backend API tests, and 4 of 5 router prompts, but blocker B1 and blocker B2 must close first.",
    "- The main release risks are router partial response and token overflow; memory guard, claim ledger, reviewer checks, and watchdog recovery reduce but do not remove uncertainty.",
    "- Rollback is documented and tested in staging with a 15 minutes target, so release can proceed only with evidence tracking and rollback monitoring active.",
    "Takeaway: conditional go with rollback, evidence, router, overflow, blocker, and uncertainty gates still enforced.",
    "",
])

JOB_OUTPUTS = {
    "research_evidence.md": "\n".join([
        "- Evidence shows 42 unit tests passed, 11 backend API tests passed, and frontend/dashboard smoke returned 200.",
        "- Router evidence is bounded: 4 of 5 prompts succeeded, with one partial response retried successfully.",
        "- Release evidence remains conditional because blocker B1 and blocker B2 are still open.",
        "Takeaway: evidence supports conditional go, not clean go.",
        "",
    ]),
    "risk_review.md": "\n".join([
        "- Token overflow is mitigated by memory guard checkpoints that avoid replaying full raw logs.",
        "- Router partial response is mitigated by watchdog classification, bounded retry, and replan instead of infinite loop.",
        "- False success remains a risk, so accepted claims require evidence refs and reviewer verification.",
        "Takeaway: risk is bounded enough for conditional go only after B1 and B2 close.",
        "",
    ]),
    "release_decision.md": "\n".join([
        "- Decision is conditional go, not clean go, because blocker B1 and blocker B2 remain unresolved.",
        "- Evidence includes 42 unit tests, 11 backend API tests, 4 of 5 router prompts, and rollback in 15 minutes.",
        "- Uncertainty remains around router behavior, overflow pressure, and missing 8-hour soak evidence.",
        "Takeaway: proceed only with rollback, evidence, router, overflow, blocker, and uncertainty gates.",
        "",
    ]),
    "summary.md": SUMMARY,
}


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def verify_summary(summary: str) -> dict:
    required = [
        "conditional go", "blocker", "evidence", "rollback", "uncertainty", "router", "overflow",
        "42 unit", "11 passed", "4 of 5", "15 minutes", "B1", "B2",
    ]
    lowered = summary.lower()
    checks = {
        "summary_file_exists": True,
        "not_placeholder": bool(summary.strip()),
        "min_bullets_met": summary.count("\n-") >= 2 or summary.startswith("-"),
        "takeaway_present": "takeaway:" in lowered,
    }
    for item in required:
        checks[f"contains:{item}"] = item.lower() in lowered
    score = round(sum(1 for value in checks.values() if value) / len(checks), 3)
    return {
        "summary_file": "summary.md",
        "score": score,
        "all_passed": all(checks.values()),
        "checks": checks,
        "metrics": {
            "word_count": len(summary.split()),
            "bullet_count": len([line for line in summary.splitlines() if line.startswith("-")]),
            "char_count": len(summary),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("spec")
    parser.add_argument("--mode", choices=["mock", "live"], default="mock")
    parser.add_argument("--out-root", default="results/ai_company_task_harness")
    args = parser.parse_args()

    if args.mode != "mock":
        raise SystemExit("This published smoke harness supports --mode mock only. Configure the full internal harness for live router mode.")

    spec_path = (ROOT / args.spec).resolve()
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    fixture = ROOT / spec.get("scope_copy_from", "tests/fixtures/ai_company_release_readiness_demo")
    run_id = f"run-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{spec.get('id', 'demo')}"
    run_dir = (ROOT / args.out_root / run_id).resolve()
    worktree = run_dir / "worktree"
    shutil.copytree(fixture, worktree)

    ai_dir = run_dir / "ai_company"
    results_dir = run_dir / "results"
    ai_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)

    meeting = {
        "run_id": run_id,
        "meeting_status": "MEETING_READY",
        "rounds_used": 1,
        "run_profile_mode": "strict",
        "task_assignments": [
            {"task_id": "job-001", "owner_role": "research_agent", "agent_profile": "research_agent", "state": "done"},
            {"task_id": "job-002", "owner_role": "risk_reviewer", "agent_profile": "risk_reviewer", "state": "done"},
            {"task_id": "job-003", "owner_role": "decision_agent", "agent_profile": "decision_agent", "state": "done"},
            {"task_id": "job-004", "owner_role": "synthesis_agent", "agent_profile": "synthesis_agent", "state": "done"},
        ],
        "discussion_log": [
            "meeting_coordinator bounded the task",
            "planner_agent assigned strict role profiles",
            "reviewer_worker required claim/evidence verification",
        ],
    }
    write_json(ai_dir / "meeting_decision.json", meeting)

    evidence_refs = [
        "release_brief.md", "test_results.md", "risk_log.md", "known_issues.md", "artifact_requirements.json",
    ]
    claims = []
    verdicts = []
    jobs = spec.get("jobs", [])
    for job in jobs:
        files = job.get("files", [])
        for rel_file in files:
            (worktree / rel_file).write_text(JOB_OUTPUTS.get(rel_file, SUMMARY), encoding="utf-8")
        (worktree / "summary.md").write_text(SUMMARY, encoding="utf-8")
        status = {
            "id": job["id"],
            "status": "SUCCESS",
            "owner_role": job.get("owner_role"),
            "agent_profile": job.get("agent_profile"),
            "profile_mode": "strict",
            "effective_policy_level": "enforced-by-wrapper + verified-posthoc",
            "effective_tool_policy": "limited",
            "files": files,
            "actual_changed_files": files,
            "actual_changed_count": len(files),
            "subagent_summary": "Mock worker produced bounded output with evidence refs.",
            "key_claims": [{"claim": "Conditional go requires evidence, rollback, router, overflow, blocker, and uncertainty gates.", "evidence_refs": evidence_refs}],
            "confidence": "medium",
            "limitations": [],
            "handoff_next": "Use claim ledger and reviewer verdict.",
        }
        write_json(results_dir / f"{job['id']}.status.json", status)
        (results_dir / f"{job['id']}.raw.txt").write_text("mock execution\n", encoding="utf-8")
        claims.append({
            "task_id": job["id"],
            "agent_profile": job.get("agent_profile"),
            "claim": status["key_claims"][0]["claim"],
            "evidence_refs": evidence_refs,
            "confidence": "medium",
        })
        verdicts.append({
            "task_id": job["id"],
            "agent_profile": job.get("agent_profile"),
            "verdict": "ACCEPTED",
            "profile_policy_status": "ok",
            "evidence": ["claim_ledger:ai_company/subagent_claim_ledger.json"],
            "repair_action": "",
        })

    claim_ledger = {
        "run_id": run_id,
        "claims": claims,
        "metrics": {"claim_count": len(claims), "claim_coverage_rate": 1.0, "uncertainty_gap_count": 0},
    }
    write_json(ai_dir / "subagent_claim_ledger.json", claim_ledger)

    reviewer = {
        "run_id": run_id,
        "verdict_count": len(verdicts),
        "accepted_count": len(verdicts),
        "false_success_blocked_count": 0,
        "profile_policy_violation_count": 0,
        "claim_ledger_metrics": claim_ledger["metrics"],
        "claim_contract": {"accepted_tasks_have_claim": True, "all_claims_have_evidence": True},
        "verdicts": verdicts,
    }
    write_json(ai_dir / "reviewer_verdicts.json", reviewer)

    artifact = verify_summary(SUMMARY)
    write_json(ai_dir / "artifact_verify_report.json", artifact)
    write_json(ai_dir / "main_agent_memory_guard_report.json", {
        "memory_guard_enabled": True,
        "memory_checkpoint_count": 0,
        "max_estimated_main_agent_tokens": 4805,
        "phases": ["after_materialize", "after_prep", "after_meeting", "after_execution", "after_post_verify"],
    })
    write_json(ai_dir / "watchdog_report.json", {"watchdog_status": "healthy", "last_action": "none", "repair_attempts_used": 0})

    report = {
        "spec_id": spec.get("id"),
        "mode": "mock",
        "run_dir": str(run_dir),
        "kpis": {
            "run_id": run_id,
            "meeting_status": "MEETING_READY",
            "execution_jobs_run": len(jobs),
            "accepted_count": len(verdicts),
            "reviewer_verdict_count": len(verdicts),
            "loop_bounded": True,
            "claim_count": len(claims),
            "claim_coverage_rate": 1.0,
            "run_profile_mode": "strict",
            "profile_attachment_rate": 1.0,
            "profile_policy_visibility_rate": 1.0,
            "profile_policy_violation_count": 0,
            "artifact_score": artifact["score"],
            "artifact_pass_rate": 1.0 if artifact["all_passed"] else 0.0,
        },
        "expectations": {"all_passed": artifact["all_passed"]},
        "overall_status": "pass" if artifact["all_passed"] else "fail",
    }
    write_json(ai_dir / "task_harness_report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["overall_status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
