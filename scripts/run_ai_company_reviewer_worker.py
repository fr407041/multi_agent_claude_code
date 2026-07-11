from __future__ import annotations

import json
import sys
from pathlib import Path

from agent_profile_resolver import PROFILE_STATUS_PROFILE_VIOLATION, profile_policy_issues
from subagent_claim_ledger import validate_claim_contract, write_claim_ledger
from verify_common_research_artifact import verify_case_dir as verify_common_case_dir
from goal_driven_workflow import verify_job_contract

ROOT = Path(__file__).resolve().parent.parent


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_scope_path(scope_path: str, run_dir: Path) -> Path:
    raw = str(scope_path or "").strip()
    if not raw:
        return run_dir / "worktree"
    if raw.startswith("/workspace/"):
        return ROOT / Path(raw.removeprefix("/workspace/"))
    path = Path(raw)
    return path if path.exists() else run_dir / "worktree"


def infer_owner_role(files: list[str], explicit_owner_role: str = "") -> str:
    if explicit_owner_role:
        return explicit_owner_role
    lowered = [item.lower() for item in files]
    if any(item.endswith((".tsx", ".jsx", ".css", ".scss", ".html")) for item in lowered):
        return "executor_frontend"
    if any(item.endswith((".md", ".txt", ".rst")) for item in lowered):
        return "executor_docs"
    return "executor_backend"


def map_verdict(
    status: str,
    verification_note: str,
    changed_count: int,
    artifact_verify: dict | None = None,
    claim_issue: str = "",
    profile_issue: str = "",
) -> tuple[str, bool]:
    if profile_issue:
        return PROFILE_STATUS_PROFILE_VIOLATION, False
    if claim_issue == "missing_claim":
        return "REPAIR_REQUIRED", False
    if claim_issue == "missing_evidence":
        return "FALSE_SUCCESS_BLOCKED", False
    if claim_issue == "uncertainty_gap":
        return "REPAIR_REQUIRED", False
    if artifact_verify and status == "SUCCESS" and not artifact_verify.get("all_passed", False):
        return "FALSE_SUCCESS_BLOCKED", False
    if status == "SUCCESS":
        return "ACCEPTED", False
    if "claimed success without verified file change" in verification_note:
        return "FALSE_SUCCESS_BLOCKED", False
    if status in {"NEEDS_REPLAN", "OVERFLOW_DETECTED", "ROUTER_ERROR", "CHILD_TIMEOUT", "CHILD_LIMIT_REACHED"}:
        return "REPLAN_REQUIRED", True
    if changed_count == 0:
        return "REPAIR_REQUIRED", False
    return "REPAIR_REQUIRED", False


def verify_summary_artifact(scope_path: Path) -> dict | None:
    if (scope_path / "artifact_requirements.json").exists():
        return verify_common_case_dir(scope_path)
    return None


def should_verify_summary_artifact(tracked_files: list[str]) -> bool:
    return "summary.md" in tracked_files


def build_reviewer_payload(run_dir: Path) -> dict:
    results_dir = run_dir / "results"
    claim_ledger = write_claim_ledger(run_dir)
    claims_by_task: dict[str, list[dict]] = {}
    for claim in claim_ledger.get("claims", []):
        claims_by_task.setdefault(str(claim.get("task_id", "")), []).append(claim)
    verdicts = []
    summary = read_json(run_dir / "summary.json") if (run_dir / "summary.json").is_file() else {}
    goal_driven = (summary.get("workflow") or {}).get("mode") == "goal_driven"
    jobs_by_id = {
        str(payload.get("id", path.stem)): payload
        for path in sorted((run_dir / "jobs").glob("job-*.json"))
        for payload in [read_json(path)]
        if "-reassign-" not in str(payload.get("id", ""))
    }
    producer_by_output = {
        str(output): job_id
        for job_id, job in jobs_by_id.items()
        for output in job.get("outputs", job.get("files", []))
    }
    for status_path in sorted(results_dir.glob("*.status.json")):
        status = read_json(status_path)
        task_id = status.get("id", status_path.stem)
        task_claims = claims_by_task.get(str(task_id), [])
        scope_path = normalize_scope_path(str(status.get("scope_path", "")), run_dir)
        tracked_files = [str(item) for item in status.get("files", [])]
        artifact_verify = None
        generic_contract = None
        job = jobs_by_id.get(str(task_id), {})
        if goal_driven and job:
            generic_contract = verify_job_contract(run_dir, job, status, task_claims)
            artifact_verify = {"all_passed": generic_contract.get("all_passed", False), "score": 1.0 if generic_contract.get("all_passed") else 0.0, "failure_category": generic_contract.get("failure_category", "")}
        elif should_verify_summary_artifact(tracked_files):
            artifact_verify = verify_summary_artifact(scope_path)
        claim_issue = ""
        if status.get("status") == "SUCCESS" and not task_claims:
            claim_issue = "missing_claim"
        elif status.get("status") == "SUCCESS" and any(not item.get("evidence_refs") for item in task_claims):
            claim_issue = "missing_evidence"
        elif status.get("status") == "SUCCESS" and any(item.get("confidence") == "high" and item.get("limitations") for item in task_claims):
            claim_issue = "uncertainty_gap"
        policy_issues = profile_policy_issues(status)
        profile_issue = ",".join(policy_issues)
        verdict, replan_required = map_verdict(
            status.get("status", "FAILED"),
            status.get("verification_note", ""),
            int(status.get("actual_changed_count", 0)),
            artifact_verify,
            claim_issue,
            profile_issue,
        )
        evidence = [
            f"status_file:{status_path.name}",
            f"raw_file:{Path(status.get('raw_file', '')).name or 'missing'}",
            f"verification_note:{status.get('verification_note', '')}",
            f"claim_count:{len(task_claims)}",
            f"claim_ledger:ai_company/subagent_claim_ledger.json",
            f"profile:{status.get('agent_profile', '') or 'missing'}",
            f"profile_mode:{status.get('profile_mode', '') or 'missing'}",
            f"profile_policy:{status.get('effective_policy_level', '') or 'missing'}",
        ]
        if policy_issues:
            evidence.append(f"profile_policy_issues:{','.join(policy_issues)}")
        if task_claims:
            claim_with_evidence = sum(1 for item in task_claims if item.get("evidence_refs"))
            evidence.append(f"claim_evidence_coverage:{claim_with_evidence}/{len(task_claims)}")
        if claim_issue:
            evidence.append(f"claim_contract_issue:{claim_issue}")
        if artifact_verify:
            evidence.append(f"artifact_score:{artifact_verify.get('score', 0.0)}")
            evidence.append(f"artifact_failure_category:{artifact_verify.get('failure_category', 'UNKNOWN')}")
        if status.get("actual_changed_count", 0):
            evidence.append(f"changed_count:{status['actual_changed_count']}")
        if status.get("test_output_file"):
            evidence.append(f"test_output:{Path(status['test_output_file']).name}")

        repair_action = ""
        if verdict == "FALSE_SUCCESS_BLOCKED":
            failure_category = artifact_verify.get("failure_category", "SUMMARY_FIDELITY_GAP") if artifact_verify else "SUMMARY_FIDELITY_GAP"
            repair_action = (
                "Worker reported success but the artifact failed quality verification. "
                f"Repair the output or narrow the prompt. Failure category: {failure_category}."
            )
            if claim_issue == "missing_evidence":
                repair_action = "Worker reported success but at least one key claim has no evidence reference. Add evidence refs or narrow the claim."
        elif verdict == PROFILE_STATUS_PROFILE_VIOLATION:
            repair_action = f"Worker output violates the assigned agent profile policy: {profile_issue}."
        elif verdict != "ACCEPTED":
            repair_action = "Rerun with narrower scope or repair artifacts depending on failure mode."
            if claim_issue == "missing_claim":
                repair_action = "Accepted subagent results must include at least one key claim with traceable evidence."
            elif claim_issue == "uncertainty_gap":
                repair_action = "Summary confidence is too strong for the stated limitations; downgrade confidence or add stronger evidence."

        verdicts.append(
            {
                "task_id": task_id,
                "review_status": "COMPLETE",
                "owner_role": infer_owner_role(status.get("files", []), str(status.get("owner_role", ""))),
                "agent_profile": status.get("agent_profile", ""),
                "profile_mode": status.get("profile_mode", ""),
                "effective_policy_level": status.get("effective_policy_level", ""),
                "profile_policy_status": "violation" if policy_issues else "ok",
                "profile_policy_notes": policy_issues,
                "verdict": verdict,
                "evidence": evidence,
                "repair_action": repair_action,
                "replan_required": replan_required,
                "failure_category": (generic_contract or {}).get("failure_category", "") or (artifact_verify or {}).get("failure_category", ""),
                "failed_job": task_id if verdict != "ACCEPTED" else "",
                "failed_checks": (generic_contract or {}).get("failed_checks", []),
                "missing_inputs": (generic_contract or {}).get("missing_inputs", []),
                "missing_artifacts": (generic_contract or {}).get("missing_artifacts", []),
                "recommended_recovery_job": next(
                    (producer_by_output[item] for item in (generic_contract or {}).get("missing_inputs", []) if item in producer_by_output),
                    task_id if verdict != "ACCEPTED" else "",
                ),
            }
        )

    accepted_task_ids = {str(item["task_id"]) for item in verdicts if item.get("verdict") == "ACCEPTED"}
    claim_contract = validate_claim_contract(claim_ledger, accepted_task_ids)
    return {
        "run_id": run_dir.name,
        "verdict_count": len(verdicts),
        "accepted_count": sum(1 for item in verdicts if item["verdict"] == "ACCEPTED"),
        "replan_required_count": sum(1 for item in verdicts if item["verdict"] == "REPLAN_REQUIRED"),
        "false_success_blocked_count": sum(1 for item in verdicts if item["verdict"] == "FALSE_SUCCESS_BLOCKED"),
        "profile_policy_violation_count": sum(1 for item in verdicts if item["verdict"] == PROFILE_STATUS_PROFILE_VIOLATION),
        "claim_ledger_metrics": claim_ledger.get("metrics", {}),
        "claim_contract": claim_contract,
        "verdicts": verdicts,
    }


def main() -> None:
    if len(sys.argv) not in {2, 3}:
        raise SystemExit("Usage: run_ai_company_reviewer_worker.py <run_dir> [out_file]")
    run_dir = Path(sys.argv[1]).resolve()
    out_file = Path(sys.argv[2]).resolve() if len(sys.argv) == 3 else None
    payload = build_reviewer_payload(run_dir)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if out_file:
        out_file.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
