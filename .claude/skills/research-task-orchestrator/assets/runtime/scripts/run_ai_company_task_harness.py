from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

from materialize_ai_company_task_run import ROOT, materialize_run, read_json
from agent_token_ledger import build_agent_token_ledger
from main_agent_memory_guard import run_guard
from run_ai_company_watchdog import write_heartbeat
from agent_profile_resolver import build_profile_summary, resolve_run_profile_mode
from package_contract import verify_package
from validate_ai_company_spec import validate_spec
from goal_driven_workflow import build_final_verdict, verify_job_contract


DEFAULTS_PATH = ROOT / "configs" / "ai_company" / "task_harness.defaults.json"
OUTPUT_ROOT = ROOT / "results" / "ai_company_task_harness"
DEFAULT_CLAUDE_CHILD_SETTINGS_PATH = ROOT / ".claude" / "settings.worker.json"


def build_preflight_report(spec_path: Path) -> dict[str, Any]:
    package_profile = os.environ.get("AI_COMPANY_PACKAGE_PROFILE", "repo").strip().lower() or "repo"
    package = verify_package(ROOT, profile=package_profile)
    spec = validate_spec(spec_path, ROOT)
    passed = bool(package.get("passed")) and bool(spec.get("passed"))
    return {
        "passed": passed,
        "package_integrity": package.get("package_integrity"),
        "release_id": package.get("release_id", ""),
        "spec_contract_status": spec.get("spec_contract_status"),
        "missing_files": package.get("missing_files", []),
        "hash_mismatches": package.get("hash_mismatches", []),
        "referenced_command_status": spec.get("referenced_command_status"),
        "worker_template_status": spec.get("worker_template_status"),
        "artifact_contract_status": spec.get("artifact_contract_status"),
        "spec_errors": spec.get("errors", []),
        "model_calls_started": 0,
    }


def fail_preflight(report: dict[str, Any], out_root: Path) -> None:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    run_dir = out_root / f"run-{stamp}-package-preflight-failure"
    ai_dir = run_dir / "ai_company"
    ai_dir.mkdir(parents=True, exist_ok=True)
    path = ai_dir / "package_preflight_report.json"
    payload = {**report, "preflight_report_file": str(path), "run_dir": str(run_dir)}
    write_json(path, payload)
    write_json(
        ai_dir / "task_harness_report.json",
        {
            "spec_id": "package-preflight-failure",
            "mode": "preflight",
            "run_dir": str(run_dir),
            "kpis": payload,
            "expectations": {"checks": {"package_preflight_passed": False}, "pass_rate": 0.0, "all_passed": False},
            "overall_status": "fail",
        },
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    raise SystemExit(2)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_defaults() -> dict[str, Any]:
    return read_json(DEFAULTS_PATH)


def run_memory_guard_phase(run_dir: Path, defaults: dict[str, Any], phase: str) -> dict[str, Any]:
    return run_guard(
        run_dir=run_dir,
        phase=phase,
        enabled=bool(defaults.get("main_agent_memory_guard_enabled", True)),
        token_threshold=int(defaults.get("main_agent_memory_token_threshold", 24000)),
        hard_threshold=int(defaults.get("main_agent_memory_hard_threshold", 32000)),
        excerpt_chars=int(defaults.get("main_agent_memory_excerpt_chars", 1200)),
        checkpoint_max_chars=int(defaults.get("main_agent_memory_checkpoint_max_chars", 8000)),
        checkpoint_dir_name=str(defaults.get("main_agent_memory_checkpoint_dir", "ai_company")),
    )


def memory_guard_report(run_dir: Path, defaults: dict[str, Any]) -> dict[str, Any]:
    report_path = run_dir / str(defaults.get("main_agent_memory_checkpoint_dir", "ai_company")) / "main_agent_memory_guard_report.json"
    return read_json(report_path) if report_path.exists() else {}


def build_token_ledger(run_dir: Path, defaults: dict[str, Any]) -> dict[str, Any]:
    threshold = int(defaults.get("agent_token_warning_threshold", defaults.get("main_agent_memory_token_threshold", 24000)))
    return build_agent_token_ledger(run_dir, threshold)


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


def run_prefetch_if_needed(spec: dict[str, Any], run_dir: Path) -> dict[str, Any] | None:
    prep_command = spec.get("prep_command")
    if not prep_command:
        return None
    argv = render_template_command(str(prep_command), run_dir)
    proc = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, check=True)
    report = {
        "command": argv,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }
    out = run_dir / "ai_company" / "prep_report.json"
    write_json(out, report)
    return report


def run_post_verify_if_needed(spec: dict[str, Any], run_dir: Path) -> dict[str, Any] | None:
    verify_command = spec.get("post_verify_command")
    if not verify_command:
        return None
    argv = render_template_command(str(verify_command), run_dir)
    proc = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, check=True)
    stdout = proc.stdout.strip()
    parsed: dict[str, Any] | None = None
    if stdout:
        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError:
            parsed = None
    report = {
        "command": argv,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "dashboard_fixture_boundary_warning": True,
        "dashboard_current_checkout_verified": False,
        "dashboard_boundary_note": "Dashboard smoke text in fixture data is historical fixture evidence, not current checkout verification.",
    }
    if parsed is not None:
        report["parsed"] = parsed
    out = run_dir / "ai_company" / "artifact_verify_report.json"
    write_json(out, report)
    return report


def calculate_false_success_block_rate(kpis: dict[str, Any]) -> float:
    accepted_count = int(kpis.get("accepted_count", 0))
    false_success_blocked_count = int(kpis.get("false_success_blocked_count", 0))
    if accepted_count + false_success_blocked_count == 0:
        return 1.0
    return round(false_success_blocked_count / (accepted_count + false_success_blocked_count), 3)


def build_generic_contract_report(run_dir: Path) -> dict[str, Any]:
    plan = read_json(run_dir / "ai_company" / "goal_plan.json")
    ledger_path = run_dir / "ai_company" / "subagent_claim_ledger.json"
    ledger = read_json(ledger_path) if ledger_path.is_file() else {"claims": []}
    claims_by_task: dict[str, list[dict[str, Any]]] = {}
    for claim in ledger.get("claims", []):
        claims_by_task.setdefault(str(claim.get("task_id", "")), []).append(claim)
    job_reports = []
    for job in plan.get("jobs", []):
        status_path = run_dir / "results" / f"{job['id']}.status.json"
        status = read_json(status_path) if status_path.is_file() else {"id": job["id"], "status": "MISSING"}
        report = verify_job_contract(run_dir, job, status, claims_by_task.get(str(job["id"]), []))
        verdict_path = run_dir / "ai_company" / "reviewer_verdicts.json"
        verdicts = read_json(verdict_path).get("verdicts", []) if verdict_path.is_file() else []
        verdict = next((item for item in verdicts if item.get("task_id") == job["id"]), {})
        report["recommended_recovery_job"] = verdict.get("recommended_recovery_job", job["id"] if not report["all_passed"] else "")
        job_reports.append(report)
    dependency_path = run_dir / "ai_company" / "dependency_state.json"
    dependency = read_json(dependency_path) if dependency_path.is_file() else {}
    payload = {
        "verification_type": "generic_contract", "all_passed": all(item["all_passed"] for item in job_reports),
        "score": round(sum(1 for item in job_reports if item["all_passed"]) / len(job_reports), 3) if job_reports else 0.0,
        "jobs": job_reports, "blocked_descendants": dependency.get("blocked_descendants", []),
    }
    write_json(run_dir / "ai_company" / "generic_contract_report.json", payload)
    return payload


def inject_job_context(run_dir: Path) -> None:
    jobs_dir = run_dir / "jobs"
    for job_path in sorted(jobs_dir.glob("job-*.json")):
        job = read_json(job_path)
        if str(job.get("worker_template", "")).strip().lower() == "summary_markdown":
            continue
        context_files = job.get("inject_context_files", [])
        if not context_files:
            continue
        blocks = []
        for rel in context_files:
            path = Path(job["scope_path"]) / rel
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if len(text) > 12000:
                text = text[:12000] + "\n[TRUNCATED]\n"
            blocks.append(f"Context file: {rel}\n{text}")
        if blocks:
            job["instruction"] = job.get("instruction", "") + "\n\nUse only the following prefetched evidence context:\n\n" + "\n\n".join(blocks)
        write_json(job_path, job)


def count_statuses(results_dir: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for path in sorted(results_dir.glob("*.status.json")):
        status = read_json(path).get("status", "UNKNOWN")
        counts[status] = counts.get(status, 0) + 1
    return counts


def build_failure_family_counts(status_counts: dict[str, int], verdict_counts: dict[str, int]) -> dict[str, int]:
    return {
        "timeout": status_counts.get("CHILD_TIMEOUT", 0),
        "router": status_counts.get("ROUTER_ERROR", 0),
        "overflow": status_counts.get("OVERFLOW_DETECTED", 0),
        "child_limit": status_counts.get("CHILD_LIMIT_REACHED", 0),
        "failed": status_counts.get("FAILED", 0),
        "needs_replan": status_counts.get("NEEDS_REPLAN", 0),
        "repair_required": verdict_counts.get("REPAIR_REQUIRED", 0),
        "replan_required": verdict_counts.get("REPLAN_REQUIRED", 0),
        "false_success_blocked": verdict_counts.get("FALSE_SUCCESS_BLOCKED", 0),
        "profile_policy_violation": verdict_counts.get("PROFILE_POLICY_VIOLATION", 0),
    }


def current_checkout_verification(run_dir: Path) -> dict[str, Any]:
    status_payloads = [read_json(path) for path in sorted((run_dir / "results").glob("*.status.json"))]
    dashboard_verified = any(
        bool((payload.get("current_checkout_verification") or {}).get("dashboard_runtime_verified"))
        for payload in status_payloads
        if isinstance(payload.get("current_checkout_verification"), dict)
    )
    repo_dashboard_present = bool(
        (ROOT / "agent_os_mvp" / "backend" / "app" / "main.py").exists()
        and (ROOT / "agent_os_mvp" / "frontend" / "package.json").exists()
        and (ROOT / "agent_os_mvp" / "frontend" / "src" / "App.jsx").exists()
    )
    skill_root_value = os.environ.get("AI_COMPANY_SKILL_ROOT", "").strip()
    skill_dashboard_root = Path(skill_root_value) / "assets" / "agent_os_mvp" if skill_root_value else Path()
    skill_dashboard_present = bool(
        skill_root_value
        and (skill_dashboard_root / "backend" / "app" / "main.py").exists()
        and (skill_dashboard_root / "frontend" / "package.json").exists()
        and (skill_dashboard_root / "frontend" / "src" / "App.jsx").exists()
    )
    dashboard_source_present = repo_dashboard_present or skill_dashboard_present
    return {
        "mock_harness_executed": True,
        "dashboard_runtime_bundled": dashboard_source_present,
        "dashboard_runtime_verified_by_mock_harness": False,
        "dashboard_source_present": dashboard_source_present,
        "dashboard_runtime_verified": dashboard_verified,
        "dashboard_runtime_reason": "Dashboard source is bundled; the mock harness does not start web services. Run the dashboard smoke command to verify runtime.",
        "dashboard_smoke_command": (
            "bash .claude/skills/research-task-orchestrator/scripts/smoke_dashboard.sh"
            if skill_dashboard_present
            else "bash agent_os_mvp/smoke-dashboard.sh"
        ),
        "dashboard_tracking_issue": None if dashboard_source_present else "#4",
    }


def evaluate_expectations(kpis: dict[str, Any], expectations: dict[str, Any]) -> dict[str, Any]:
    checks: dict[str, bool] = {}
    if "meeting_status" in expectations:
        checks["meeting_status_match"] = kpis["meeting_status"] == expectations["meeting_status"]
    if "min_reviewer_verdict_count" in expectations:
        checks["min_reviewer_verdict_count_met"] = kpis["reviewer_verdict_count"] >= int(expectations["min_reviewer_verdict_count"])
    if "min_accepted_count" in expectations:
        checks["min_accepted_count_met"] = kpis["accepted_count"] >= int(expectations["min_accepted_count"])
    if "min_replan_required_count" in expectations:
        checks["min_replan_required_count_met"] = kpis["replan_required_count"] >= int(expectations["min_replan_required_count"])
    if "max_reassignment_count" in expectations:
        checks["max_reassignment_count_met"] = kpis["reassignment_count"] <= int(expectations["max_reassignment_count"])
    if "max_execution_jobs_run" in expectations:
        checks["max_execution_jobs_run_met"] = kpis["execution_jobs_run"] <= int(expectations["max_execution_jobs_run"])
    if "require_bounded_scope_rate" in expectations:
        checks["bounded_scope_rate_met"] = float(kpis["bounded_scope_rate"]) >= float(expectations["require_bounded_scope_rate"])
    if "require_status_file_coverage_rate" in expectations:
        checks["status_file_coverage_rate_met"] = float(kpis["status_file_coverage_rate"]) >= float(expectations["require_status_file_coverage_rate"])
    if expectations.get("require_loop_bounded") is True:
        checks["loop_bounded_met"] = bool(kpis["loop_bounded"])
    if expectations.get("require_live_meeting") is True:
        checks["live_meeting_used"] = bool(kpis.get("live_meeting_used", False))
        checks["live_meeting_not_degraded"] = not bool(kpis.get("live_degraded", False))
        checks["live_meeting_role_turns_met"] = int(kpis.get("live_turn_count", 0)) >= 4
    artifact_verify = kpis.get("artifact_verify", {})
    artifact_parsed = artifact_verify.get("parsed", {}) if isinstance(artifact_verify, dict) else {}
    if expectations.get("require_post_verify_all_passed") is True:
        checks["post_verify_all_passed"] = bool(artifact_parsed.get("all_passed", False))
    if "min_artifact_score" in expectations:
        checks["min_artifact_score_met"] = float(artifact_parsed.get("score", 0.0)) >= float(expectations["min_artifact_score"])
    if expectations.get("require_evidence_gate_passed") is True:
        checks["evidence_gate_passed"] = bool(((artifact_parsed.get("evidence_integrity") or {}).get("all_passed", False)))
    if expectations.get("require_summary_fidelity_passed") is True:
        checks["summary_fidelity_passed"] = bool(((artifact_parsed.get("summary_fidelity") or {}).get("all_passed", False)))
    if "min_evidence_coverage_rate" in expectations:
        checks["evidence_coverage_rate_met"] = float(artifact_parsed.get("metrics", {}).get("evidence_coverage_rate", 0.0)) >= float(expectations["min_evidence_coverage_rate"])
    if expectations.get("require_dated_claim_coverage") is True:
        checks["dated_claim_coverage_met"] = float(artifact_parsed.get("metrics", {}).get("dated_claim_coverage", 0.0)) >= 1.0
    if expectations.get("require_price_reaction_coverage") is True:
        checks["price_reaction_coverage_met"] = float(artifact_parsed.get("metrics", {}).get("price_reaction_coverage", 0.0)) >= 1.0
    if expectations.get("require_uncertainty_statement_presence") is True:
        checks["uncertainty_statement_presence_met"] = float(artifact_parsed.get("metrics", {}).get("uncertainty_statement_presence", 0.0)) >= 1.0
    if expectations.get("require_no_reviewer_quality_gap") is True:
        checks["no_reviewer_quality_gap"] = bool(kpis.get("reviewer_quality_consistency", True))
    if "require_profile_mode" in expectations:
        checks["profile_mode_match"] = str(kpis.get("run_profile_mode", "")) == str(expectations["require_profile_mode"])
    if expectations.get("require_profile_attachment") is True:
        checks["profile_attachment_complete"] = float(kpis.get("profile_attachment_rate", 0.0)) >= 1.0
    if expectations.get("require_no_profile_policy_violations") is True:
        checks["no_profile_policy_violations"] = int(kpis.get("profile_policy_violation_count", 0)) == 0
    return {
        "checks": checks,
        "pass_rate": round(sum(1 for ok in checks.values() if ok) / len(checks), 3) if checks else 1.0,
        "all_passed": all(checks.values()) if checks else True,
    }


def build_kpis(run_dir: Path, spec: dict[str, Any], meeting: dict[str, Any], execution: dict[str, Any], reviewer: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    jobs = spec.get("jobs", [])
    results_dir = run_dir / "results"
    status_files = sorted(results_dir.glob("*.status.json"))
    status_counts = count_statuses(results_dir)
    non_review_job_count = len(jobs)
    expected_status_files_floor = non_review_job_count
    max_reassign = int(defaults.get("max_reassignments_per_run", 1))
    max_execution_jobs = non_review_job_count * (1 + max_reassign)

    bounded_scope = [
        len(item.get("scope", [])) <= int(defaults.get("max_scope_files", 3))
        for item in meeting.get("task_assignments", [])
        if item.get("owner_role") != "reviewer_worker"
    ]
    verdict_counts: dict[str, int] = {}
    for item in reviewer.get("verdicts", []):
        verdict = str(item.get("verdict", "UNKNOWN"))
        verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1
    failure_family_counts = build_failure_family_counts(status_counts, verdict_counts)
    memory_report = memory_guard_report(run_dir, defaults)
    claim_metrics = reviewer.get("claim_ledger_metrics", {})
    claim_contract = reviewer.get("claim_contract", {})
    profile_summary = build_profile_summary(run_dir)
    agent_profiles = profile_summary.get("agent_profiles", [])
    attached_profiles = [item for item in agent_profiles if item.get("agent_profile")]
    strict_profiles = [item for item in agent_profiles if item.get("profile_mode") == "strict"]

    kpis = {
        "run_id": run_dir.name,
        "goal": spec["goal"],
        "meeting_status": meeting.get("meeting_status"),
        "meeting_mode": meeting.get("meeting_mode", "deterministic"),
        "requested_meeting_mode": meeting.get("requested_meeting_mode", "deterministic"),
        "live_meeting_used": bool(meeting.get("live_meeting_used", False)),
        "live_turn_count": int(meeting.get("live_turn_count", 0)),
        "live_transport": meeting.get("live_transport", ""),
        "live_transport_reason": meeting.get("live_transport_reason", ""),
        "live_degraded": bool(meeting.get("live_degraded", False)),
        "live_degrade_category": str(meeting.get("degrade_category", "")),
        "live_meeting_transcript_file": str(meeting.get("transcript_file", "")),
        "meeting_rounds_used": meeting.get("rounds_used", 0),
        "non_review_job_count": non_review_job_count,
        "execution_jobs_run": execution.get("execution_jobs_run", 0),
        "reassignment_count": execution.get("reassignment_count", 0),
        "accepted_count": reviewer.get("accepted_count", 0),
        "reviewer_verdict_count": reviewer.get("verdict_count", 0),
        "replan_required_count": reviewer.get("replan_required_count", 0),
        "repair_required_count": verdict_counts.get("REPAIR_REQUIRED", 0),
        "false_success_blocked_count": reviewer.get("false_success_blocked_count", 0),
        "status_file_count": len(status_files),
        "status_file_coverage_rate": round(len(status_files) / expected_status_files_floor, 3) if expected_status_files_floor else 1.0,
        "bounded_scope_rate": round(sum(1 for ok in bounded_scope if ok) / len(bounded_scope), 3) if bounded_scope else 1.0,
        "loop_bounded": execution.get("execution_jobs_run", 0) <= max_execution_jobs,
        "status_counts": status_counts,
        "verdict_counts": verdict_counts,
        "failure_family_counts": failure_family_counts,
        "successful_execution_rate": round(reviewer.get("accepted_count", 0) / reviewer.get("verdict_count", 1), 3) if reviewer.get("verdict_count", 0) else 0.0,
        "max_allowed_execution_jobs": max_execution_jobs,
        "memory_guard_enabled": bool(defaults.get("main_agent_memory_guard_enabled", True)),
        "checkpoint_threshold_tokens": int(memory_report.get("checkpoint_threshold_tokens", defaults.get("main_agent_memory_token_threshold", 24000))),
        "hard_threshold_tokens": int(memory_report.get("hard_threshold_tokens", defaults.get("main_agent_memory_hard_threshold", 32000))),
        "threshold_source": str(memory_report.get("threshold_source", DEFAULTS_PATH.relative_to(ROOT).as_posix())),
        "memory_checkpoint_count": int(memory_report.get("memory_checkpoint_count", 0)),
        "memory_checkpoint_decision": str(memory_report.get("checkpoint_decision", "unknown")),
        "memory_checkpoint_policy_result": str(memory_report.get("checkpoint_policy_result", "unknown")),
        "max_estimated_main_agent_tokens": int(memory_report.get("max_estimated_main_agent_tokens", 0)),
        "checkpoint_compression_rate": float(memory_report.get("checkpoint_compression_rate", 1.0)),
        "claim_count": int(claim_metrics.get("claim_count", 0)),
        "claim_coverage_rate": float(claim_metrics.get("claim_coverage_rate", 0.0)),
        "uncertainty_gap_count": int(claim_metrics.get("uncertainty_gap_count", 0)),
        "accepted_claim_contract_met": bool(
            claim_contract.get("all_claims_have_evidence", True)
            and claim_contract.get("accepted_tasks_have_claim", True)
        ),
        "run_profile_mode": profile_summary.get("run_profile_mode", "unknown"),
        "agent_profile_count": len(agent_profiles),
        "strict_agent_profile_count": len(strict_profiles),
        "profile_attachment_rate": round(len(attached_profiles) / len(agent_profiles), 3) if agent_profiles else 1.0,
        "profile_policy_visibility_rate": 1.0 if all(item.get("effective_policy_level") for item in agent_profiles) else 0.0,
        "profile_policy_violation_count": int(reviewer.get("profile_policy_violation_count", 0)) + len(profile_summary.get("policy_violations", [])),
        "profile_downgrade_count": len(profile_summary.get("downgrade_summary", [])),
        "agent_profiles": agent_profiles,
        "policy_violations": profile_summary.get("policy_violations", []),
        "downgrade_summary": profile_summary.get("downgrade_summary", []),
    }
    return kpis


def build_sens_evaluation_report(run_dir: Path, spec: dict[str, Any], kpis: dict[str, Any], reviewer: dict[str, Any], expectation_report: dict[str, Any]) -> dict[str, Any]:
    artifact_verify = kpis.get("artifact_verify", {})
    artifact = artifact_verify.get("parsed", {}) if isinstance(artifact_verify, dict) else {}
    evidence = artifact.get("evidence_integrity", {})
    fidelity = artifact.get("summary_fidelity", {})
    failure_family_counts = kpis.get("failure_family_counts", {})

    reviewer_verdicts = reviewer.get("verdicts", [])
    accepted_count = sum(1 for item in reviewer_verdicts if item.get("verdict") == "ACCEPTED")
    reviewer_quality_gap = accepted_count > 0 and not artifact.get("all_passed", False)
    blocking_families = {key: int(value) for key, value in failure_family_counts.items() if int(value) > 0}

    process_score = round(
        (
            (1.0 if kpis.get("meeting_rounds_used", 0) <= 3 else 0.0)
            + (1.0 if kpis.get("loop_bounded") else 0.0)
            + (1.0 if kpis.get("execution_jobs_run", 0) <= kpis.get("max_allowed_execution_jobs", 0) else 0.0)
        )
        / 3,
        3,
    )
    content_score = round(
        (
            float(artifact.get("score", 0.0))
            + float(artifact.get("metrics", {}).get("evidence_coverage_rate", 0.0))
            + float(artifact.get("metrics", {}).get("dated_claim_coverage", 0.0))
            + float(artifact.get("metrics", {}).get("price_reaction_coverage", 0.0))
            + float(artifact.get("metrics", {}).get("uncertainty_statement_presence", 0.0))
        )
        / 5,
        3,
    )
    trust_score = round(
        (
            (1.0 if evidence.get("all_passed", False) else 0.0)
            + (1.0 if not reviewer_quality_gap else 0.0)
            + (1.0 if not blocking_families else 0.0)
        )
        / 3,
        3,
    )
    overall_score = round((process_score + content_score + trust_score) / 3, 3)

    if not evidence.get("all_passed", False):
        failure_category = "evidence_insufficient"
    elif reviewer_quality_gap:
        failure_category = "reviewer_quality_gap"
    elif blocking_families.get("router", 0) > 0:
        failure_category = "router_issue"
    elif blocking_families.get("overflow", 0) > 0:
        failure_category = "model_behavior_unstable"
    elif artifact.get("failure_category") and artifact.get("failure_category") != "PASS":
        if artifact["failure_category"] in {"MISSING_PRICE_REACTION", "MISSING_UNCERTAINTY", "OUTPUT_CONTRACT_VIOLATION", "MISSING_REQUIRED_DATES", "MISSING_COMPENSATION_CAVEAT"}:
            failure_category = "prompt_design_gap"
        else:
            failure_category = "summary_quality_gap"
    else:
        failure_category = "pass"

    deliverable_ready = bool(expectation_report.get("all_passed", False)) and not reviewer_quality_gap and not blocking_families
    report = {
        "spec_id": spec.get("id"),
        "run_id": run_dir.name,
        "goal": spec.get("goal"),
        "evidence_completeness": {
            "all_passed": evidence.get("all_passed", False),
            "score": evidence.get("score", 0.0),
            "checks": evidence.get("checks", {}),
        },
        "summary_fidelity": {
            "all_passed": fidelity.get("all_passed", False),
            "score": fidelity.get("score", 0.0),
            "checks": fidelity.get("checks", {}),
        },
        "reviewer_verdict_consistency": {
            "accepted_count": accepted_count,
            "false_success_blocked_count": reviewer.get("false_success_blocked_count", 0),
            "reviewer_quality_gap": reviewer_quality_gap,
        },
        "artifact_verify_result": {
            "all_passed": artifact.get("all_passed", False),
            "score": artifact.get("score", 0.0),
            "failure_category": artifact.get("failure_category", "UNKNOWN"),
        },
        "scores": {
            "process_score": process_score,
            "content_score": content_score,
            "trust_score": trust_score,
            "overall_score": overall_score,
        },
        "kpi_snapshot": {
            "meeting_convergence_rate": 1.0 if kpis.get("meeting_rounds_used", 0) <= 3 else 0.0,
            "bounded_execution_rate": 1.0 if kpis.get("execution_jobs_run", 0) <= kpis.get("max_allowed_execution_jobs", 0) else 0.0,
            "loop_bounded_rate": 1.0 if kpis.get("loop_bounded") else 0.0,
            "artifact_pass_rate": 1.0 if artifact.get("all_passed", False) else 0.0,
            "artifact_score": artifact.get("score", 0.0),
            "evidence_coverage_rate": artifact.get("metrics", {}).get("evidence_coverage_rate", 0.0),
            "dated_claim_coverage": artifact.get("metrics", {}).get("dated_claim_coverage", 0.0),
            "price_reaction_coverage": artifact.get("metrics", {}).get("price_reaction_coverage", 0.0),
            "uncertainty_statement_presence": artifact.get("metrics", {}).get("uncertainty_statement_presence", 0.0),
            "false_success_block_rate": calculate_false_success_block_rate(kpis),
        },
        "failure_category": failure_category,
        "deliverable_ready": deliverable_ready,
        "company_readiness": "deliverable" if deliverable_ready else "not_deliverable",
        "defect_classification": failure_category,
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Run generic ai-company task harness and emit KPI report.")
    parser.add_argument("spec", help="Path to a generic task spec JSON file.")
    parser.add_argument("--mode", choices=["live", "mock"], default="mock")
    parser.add_argument("--out-root", default=str(OUTPUT_ROOT))
    parser.add_argument("--resume-run", default="", help="Resume an interrupted materialized run directory.")
    args = parser.parse_args()

    defaults = load_defaults()
    spec_path = Path(args.spec).resolve()
    out_root = Path(args.out_root).resolve()
    preflight = build_preflight_report(spec_path)
    if not preflight["passed"]:
        fail_preflight(preflight, out_root)
    spec = read_json(spec_path)
    if (spec.get("workflow") or {}).get("mode") == "goal_driven" and not spec.get("jobs"):
        spec["jobs"] = (spec.get("goal_plan") or {}).get("jobs", [])
    spec.setdefault("agent_profile_mode", defaults.get("agent_profile_mode", "auto"))
    resolved_mode, profile_reasons = resolve_run_profile_mode(spec)
    spec["agent_profile_mode"] = resolved_mode
    spec["profile_resolution_reasons"] = profile_reasons
    resumed = bool(args.resume_run)
    if resumed:
        run_dir = Path(args.resume_run).resolve()
        if not (run_dir / "ai_company" / "meeting_decision.json").is_file():
            fail_preflight(
                {
                    **preflight,
                    "passed": False,
                    "spec_contract_status": "RESUME_RUN_INVALID",
                    "spec_errors": [{"code": "MEETING_DECISION_MISSING", "detail": str(run_dir)}],
                },
                out_root,
            )
        write_json(run_dir / "ai_company" / "package_preflight_report.json", preflight)
        write_heartbeat(run_dir, "resume_execution", "running")
    else:
        run_dir = materialize_run(spec_path, out_root)
        write_json(run_dir / "ai_company" / "package_preflight_report.json", preflight)
        write_heartbeat(run_dir, "after_materialize", "running")
        run_memory_guard_phase(run_dir, defaults, "after_materialize")
        run_prefetch_if_needed(spec, run_dir)
        write_heartbeat(run_dir, "after_prep", "running")
        run_memory_guard_phase(run_dir, defaults, "after_prep")
        inject_job_context(run_dir)

    env = os.environ.copy()
    env["AI_COMPANY_MEETING_ROUND_LIMIT"] = str(defaults.get("meeting_round_limit", 3))
    env["AI_COMPANY_MAX_REASSIGNMENTS_PER_RUN"] = str(
        (spec.get("workflow") or {}).get("max_replans", defaults.get("max_reassignments_per_run", 1))
    )
    env.setdefault("CLAUDE_CHILD_TIMEOUT_SEC", str(defaults.get("child_timeout_sec", 60)))
    env.setdefault("AI_COMPANY_CALL_TIMEOUT_SEC", str(defaults.get("call_timeout_sec", 90)))
    if defaults.get("claude_model_alias"):
        env.setdefault("CLAUDE_MODEL_ALIAS", str(defaults.get("claude_model_alias")))
    if defaults.get("live_model"):
        env.setdefault("CCR_PREFERRED_MODEL", str(defaults.get("live_model")))
    env.setdefault("CCR_MAX_OUTPUT_TOKENS", str(defaults.get("router_max_output_tokens", 1024)))
    env.setdefault("AI_COMPANY_MEETING_MODE", str(defaults.get("meeting_mode", "deterministic")))
    env.setdefault("CLAUDE_TOOLS_VALUE", str(defaults.get("claude_tools_value", "")))
    if DEFAULT_CLAUDE_CHILD_SETTINGS_PATH.exists():
        env.setdefault("CLAUDE_CHILD_SETTINGS_PATH", str(DEFAULT_CLAUDE_CHILD_SETTINGS_PATH))
    if args.mode == "mock":
        env["AI_COMPANY_EXECUTION_MOCK"] = "1"

    meeting_path = run_dir / "ai_company" / "meeting_decision.json"
    if not resumed:
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "run_ai_company_meeting.py"), str(run_dir), str(meeting_path), spec["goal"]],
            check=True,
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        write_heartbeat(run_dir, "after_meeting", "running")
        run_memory_guard_phase(run_dir, defaults, "after_meeting")

    execution_path = run_dir / "ai_company" / "execution_summary.json"
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "run_ai_company_execution.py"), str(run_dir), str(execution_path)],
        check=True,
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    write_heartbeat(run_dir, "after_execution", "running")
    run_memory_guard_phase(run_dir, defaults, "after_execution")

    meeting = read_json(meeting_path)
    execution = read_json(execution_path)
    reviewer = read_json(run_dir / "ai_company" / "reviewer_verdicts.json")
    token_ledger = build_token_ledger(run_dir, defaults)
    kpis = build_kpis(run_dir, spec, meeting, execution, reviewer, defaults)
    kpis.update(
        {
            "package_integrity": preflight.get("package_integrity"),
            "release_id": preflight.get("release_id", ""),
            "spec_contract_status": preflight.get("spec_contract_status"),
            "artifact_contract_status": preflight.get("artifact_contract_status"),
            "worker_template_status": preflight.get("worker_template_status"),
            "referenced_command_status": preflight.get("referenced_command_status"),
            "agent_token_ledger_file": "ai_company/agent_token_ledger.json",
            "agent_token_warning_threshold": token_ledger.get("warning_threshold_tokens", 0),
            "total_estimated_agent_tokens": token_ledger.get("total_estimated_agent_tokens", 0),
            "total_provider_agent_tokens": token_ledger.get("total_provider_agent_tokens", 0),
            "total_effective_agent_tokens": token_ledger.get("total_effective_agent_tokens", 0),
            "max_agent_turn_tokens": token_ledger.get("max_agent_turn_tokens", 0),
            "top_token_agent": token_ledger.get("top_token_agent", ""),
            "overflow_risk_agent_count": token_ledger.get("overflow_risk_agent_count", 0),
        }
    )
    checkout_verification = current_checkout_verification(run_dir)
    artifact_verify = run_post_verify_if_needed(spec, run_dir)
    write_heartbeat(run_dir, "after_post_verify", "running")
    run_memory_guard_phase(run_dir, defaults, "after_post_verify")
    kpis.update(
        {
            "checkpoint_threshold_tokens": int(memory_guard_report(run_dir, defaults).get("checkpoint_threshold_tokens", defaults.get("main_agent_memory_token_threshold", 24000))),
            "hard_threshold_tokens": int(memory_guard_report(run_dir, defaults).get("hard_threshold_tokens", defaults.get("main_agent_memory_hard_threshold", 32000))),
            "threshold_source": str(memory_guard_report(run_dir, defaults).get("threshold_source", DEFAULTS_PATH.relative_to(ROOT).as_posix())),
            "memory_checkpoint_count": int(memory_guard_report(run_dir, defaults).get("memory_checkpoint_count", 0)),
            "memory_checkpoint_decision": str(memory_guard_report(run_dir, defaults).get("checkpoint_decision", "unknown")),
            "memory_checkpoint_policy_result": str(memory_guard_report(run_dir, defaults).get("checkpoint_policy_result", "unknown")),
            "max_estimated_main_agent_tokens": int(memory_guard_report(run_dir, defaults).get("max_estimated_main_agent_tokens", 0)),
            "checkpoint_compression_rate": float(memory_guard_report(run_dir, defaults).get("checkpoint_compression_rate", 1.0)),
        }
    )
    if artifact_verify is not None:
        kpis["artifact_verify"] = artifact_verify
        artifact_parsed = artifact_verify.get("parsed", {}) if isinstance(artifact_verify, dict) else {}
        artifact_metrics = artifact_parsed.get("metrics", {}) if isinstance(artifact_parsed, dict) else {}
        kpis["artifact_pass_rate"] = 1.0 if artifact_parsed.get("all_passed", False) else 0.0
        kpis["artifact_score"] = float(artifact_parsed.get("score", 0.0))
        kpis["evidence_coverage_rate"] = float(artifact_metrics.get("evidence_coverage_rate", 0.0))
        kpis["dated_claim_coverage"] = float(artifact_metrics.get("dated_claim_coverage", 0.0))
        kpis["price_reaction_coverage"] = float(artifact_metrics.get("price_reaction_coverage", 0.0))
        kpis["uncertainty_statement_presence"] = float(artifact_metrics.get("uncertainty_statement_presence", 0.0))
        kpis["evidence_gate_passed"] = bool((artifact_parsed.get("evidence_integrity") or {}).get("all_passed", False))
        kpis["summary_fidelity_passed"] = bool((artifact_parsed.get("summary_fidelity") or {}).get("all_passed", False))
        kpis["reviewer_quality_consistency"] = not (reviewer.get("accepted_count", 0) > 0 and not artifact_parsed.get("all_passed", False))
        kpis["false_success_block_rate"] = calculate_false_success_block_rate(kpis)
    expectation_report = evaluate_expectations(kpis, spec.get("expectations", {}))
    evaluation_report = build_sens_evaluation_report(run_dir, spec, kpis, reviewer, expectation_report)
    write_json(run_dir / "ai_company" / "sens_evaluation_report.json", evaluation_report)

    workflow_mode = str((spec.get("workflow") or {}).get("mode", "fixed"))
    final_verdict = None
    if workflow_mode == "goal_driven":
        generic_report = build_generic_contract_report(run_dir)
        dag_report = read_json(run_dir / "ai_company" / "dag_validation_report.json")
        recovery_path = run_dir / "ai_company" / "recovery_trace.jsonl"
        recovery_events = [json.loads(line) for line in recovery_path.read_text(encoding="utf-8").splitlines() if line.strip()] if recovery_path.is_file() else []
        final_verdict = build_final_verdict(run_dir.name, dag_report, generic_report, recovery_events)
        if not expectation_report.get("all_passed", False):
            final_verdict.update({
                "overall_status": "fail",
                "failure_category": final_verdict.get("failure_category") or "HARNESS_CONTRACT_FAILED",
                "failed_checks": [key for key, passed in expectation_report.get("checks", {}).items() if not passed],
                "next_action": "Repair harness safety or bounded-execution checks before delivery.",
            })
        write_json(run_dir / "ai_company" / "final_run_verdict.json", final_verdict)
        kpis.update({
            "workflow_mode": workflow_mode,
            "generic_contract_score": generic_report.get("score", 0.0),
            "generic_contract_passed": generic_report.get("all_passed", False),
            "root_failed_job": final_verdict.get("root_failed_job", ""),
            "blocked_descendant_count": len(final_verdict.get("blocked_descendants", [])),
        })

    report = {
        "spec_id": spec["id"],
        "mode": args.mode,
        "run_dir": str(run_dir),
        "defaults_file": str(DEFAULTS_PATH),
        "spec_file": str(spec_path),
        "resumed": resumed,
        "kpis": kpis,
        "current_checkout_verification": checkout_verification,
        "expectations": expectation_report,
        "overall_status": final_verdict["overall_status"] if final_verdict is not None else ("pass" if expectation_report["all_passed"] else "fail"),
        "final_verdict_file": "ai_company/final_run_verdict.json" if final_verdict is not None else "",
    }
    report_path = run_dir / "ai_company" / "task_harness_report.json"
    write_json(report_path, report)
    write_heartbeat(run_dir, "complete", "complete")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
