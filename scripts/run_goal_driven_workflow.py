from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from goal_driven_workflow import deterministic_goal_plan, normalize_goal_plan, validate_goal_plan, write_json
from run_ai_company_meeting import call_live_provider_for_turn, resolve_live_meeting_transport


ROOT = Path(__file__).resolve().parents[1]


def extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    decoder = json.JSONDecoder()
    for index, char in enumerate(stripped):
        if char != "{":
            continue
        try:
            payload, _ = decoder.raw_decode(stripped[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and isinstance(payload.get("jobs"), list):
            return payload
    raise ValueError("planner response did not contain a goal DAG JSON object")


def live_goal_plan(
    goal: str,
    supplied_inputs: list[str],
    max_jobs: int,
    artifact_dir: Path,
    response_file: str = "",
    prior_plan: dict[str, Any] | None = None,
    validation_errors: list[dict[str, Any]] | None = None,
    attempt: int = 1,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if response_file:
        source = Path(response_file).resolve()
        text = source.read_text(encoding="utf-8")
        return normalize_goal_plan(goal, extract_json_object(text), max_jobs, supplied_inputs), {
            "mode": "live_resume", "transport": "recorded_provider_response", "transport_reason": "resume_after_deterministic_validation",
            "provider_usage": {}, "raw_artifact": str(source),
        }
    transport, reason = resolve_live_meeting_transport()
    repair_context = ""
    if prior_plan is not None:
        repair_context = f"""
This is bounded planner repair attempt {attempt}. Repair the prior plan; do not merely repeat it.
Prior normalized plan:
{json.dumps(prior_plan, ensure_ascii=False)}
Deterministic validation errors:
{json.dumps(validation_errors or [], ensure_ascii=False)}
Preserve the goal and required evidence. Fix every listed error without weakening acceptance criteria.
"""
    prompt = f"""Return only one strict JSON object. No markdown and no explanation.

Plan a bounded executable DAG for this goal:
{goal}

Supplied input paths: {json.dumps(supplied_inputs)}
Maximum jobs: {max_jobs}
Allowed capabilities: acquire, analyze, generate, execute, verify, synthesize.
Allowed tools: none, read, write, network, python, shell, test.
Each job must include id, title, capability, instruction, depends_on, inputs, outputs, tools,
acceptance_criteria, evidence_requirements, and test_command (empty unless command_success is required).
An acquire job using network must include source_url. Only runtime-allowlisted hosts are accepted.
Each acceptance criterion must be an object using one of:
artifact_exists, json_valid, json_path_equals, numeric_compare, command_success,
derived_provenance, claim_evidence, goal_answering.
For evidence consumed downstream, require non_empty, json_min_items, json_required_paths, or text_min_chars as appropriate.
Use only relative safe paths. Every non-supplied input must have exactly one upstream producer.
Keep each job to at most two outputs. End with a synthesize job that answers the goal.
For one output use a single-file job. For two outputs, both artifacts must be independently machine-verifiable.
{repair_context}
"""
    result = call_live_provider_for_turn(transport, "planner_agent", {"goal": goal}, prompt, artifact_dir)
    raw_file = artifact_dir / f"goal_planner.attempt-{attempt:03d}.raw.txt"
    raw_file.parent.mkdir(parents=True, exist_ok=True)
    raw_file.write_text(result.text, encoding="utf-8")
    payload = extract_json_object(result.text)
    metadata = {
        "mode": "live", "transport": transport, "transport_reason": reason,
        "provider_usage": result.provider_usage, "raw_artifact": str(raw_file), "attempt": attempt,
    }
    return normalize_goal_plan(goal, payload, max_jobs, supplied_inputs), metadata


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate and execute a bounded goal-driven ai-company DAG.")
    parser.add_argument("--goal", required=True)
    parser.add_argument("--mode", choices=["live", "mock"], default="mock")
    parser.add_argument("--out-root", default=str(ROOT / "results" / "ai_company_task_harness"))
    parser.add_argument("--scope-copy-from", default="")
    parser.add_argument("--supplied-input", action="append", default=[])
    parser.add_argument("--max-jobs", type=int, default=8)
    parser.add_argument("--max-replans", type=int, default=2)
    parser.add_argument("--planner-response-file", default="", help="Resume a previously recorded live planner response after deterministic validation changes.")
    args = parser.parse_args()

    out_root = Path(args.out_root).resolve()
    generated_dir = out_root / ".generated_specs"
    generated_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    artifact_dir = generated_dir / f"goal-{stamp}-artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    planner_attempts: list[dict[str, Any]] = []
    if args.mode == "live":
        plan = {}
        dag_report = {"passed": False, "errors": []}
        prior_plan: dict[str, Any] | None = None
        errors: list[dict[str, Any]] = []
        seen_planner_fingerprints: set[str] = set()
        for attempt in range(1, args.max_replans + 2):
            try:
                plan, planner_metadata = live_goal_plan(
                    args.goal, args.supplied_input, args.max_jobs, artifact_dir,
                    args.planner_response_file if attempt == 1 else "", prior_plan, errors, attempt,
                )
                dag_report = validate_goal_plan(plan, args.max_jobs)
                dag_report["model_calls_started"] = attempt
            except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
                plan = prior_plan or {"goal": args.goal, "supplied_inputs": args.supplied_input, "jobs": []}
                dag_report = {
                    "passed": False, "status": "PLANNER_RESPONSE_INVALID", "job_count": len(plan.get("jobs", [])),
                    "topological_order": [], "errors": [{"code": "PLANNER_RESPONSE_INVALID", "detail": str(exc)}],
                    "model_calls_started": attempt,
                }
                planner_metadata = {"mode": "live", "attempt": attempt, "error": str(exc), "raw_artifact": ""}
            stable_errors = [{"code": item.get("code", ""), "detail": item.get("detail", "")} for item in dag_report.get("errors", [])]
            planner_fingerprint = hashlib.sha256(json.dumps({"plan": plan, "errors": stable_errors}, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
            planner_metadata["planner_fingerprint"] = planner_fingerprint
            unchanged = not dag_report["passed"] and planner_fingerprint in seen_planner_fingerprints
            if unchanged:
                dag_report["errors"] = [*dag_report.get("errors", []), {"code": "UNCHANGED_PLANNER_REPAIR_BLOCKED", "detail": planner_fingerprint}]
                dag_report["status"] = "UNCHANGED_PLANNER_REPAIR_BLOCKED"
            seen_planner_fingerprints.add(planner_fingerprint)
            write_json(artifact_dir / f"goal_plan.attempt-{attempt:03d}.json", plan)
            write_json(artifact_dir / f"dag_validation_report.attempt-{attempt:03d}.json", dag_report)
            planner_attempts.append({**planner_metadata, "validation": dag_report})
            if dag_report["passed"]:
                break
            if unchanged:
                break
            prior_plan = plan
            errors = list(dag_report.get("errors", []))
    else:
        plan = deterministic_goal_plan(args.goal, args.supplied_input)
        planner_metadata = {"mode": "mock", "transport": "deterministic", "raw_artifact": ""}
        dag_report = validate_goal_plan(plan, args.max_jobs)
        planner_attempts = [{**planner_metadata, "attempt": 1, "validation": dag_report}]
    write_json(artifact_dir / "goal_plan.json", plan)
    write_json(artifact_dir / "dag_validation_report.json", dag_report)
    if not dag_report["passed"]:
        final_verdict = {
            "overall_status": "fail", "failure_category": "PLANNER_CONTRACT_EXHAUSTED",
            "root_failed_job": "planner_agent", "failed_checks": dag_report.get("errors", []),
            "blocked_descendants": [], "recovery_action": "planner_repair_budget_exhausted",
            "next_action": "Review the final DAG validation errors and narrow or clarify the goal.",
            "planner_attempt_count": len(planner_attempts), "model_calls_started": len(planner_attempts) if args.mode == "live" else 0,
            "source": "canonical_final_run_verdict",
        }
        write_json(artifact_dir / "planner_attempts.json", {"attempts": planner_attempts})
        write_json(artifact_dir / "final_run_verdict.json", final_verdict)
        print(json.dumps({"goal_plan": plan, "dag_validation": dag_report, "planner_attempts": planner_attempts, "final_run_verdict": final_verdict, "artifact_dir": str(artifact_dir)}, ensure_ascii=False, indent=2))
        return 2

    spec = {
        "id": f"goal-driven-{stamp}",
        "goal": args.goal,
        "strategy": "Execute a validated dependency DAG and repair the earliest failed producer.",
        "workflow": {"mode": "goal_driven", "max_jobs": args.max_jobs, "max_replans": args.max_replans},
        "verification": {"type": "generic_contract", "claim_evidence_required": True, "provenance_required": True},
        "recovery": {"strategy": "dependency_aware", "max_attempts_per_job": 2},
        "agent_profile_mode": "strict",
        "scope_copy_from": args.scope_copy_from,
        "jobs": plan["jobs"],
        "goal_plan": plan,
        "dag_validation_report": dag_report,
        "goal_planner": {**planner_metadata, "attempt_count": len(planner_attempts), "attempts": planner_attempts},
        "expectations": {"meeting_status": "MEETING_READY", "loop_bounded": True, "max_execution_jobs_run": args.max_jobs + args.max_replans},
    }
    spec_path = generated_dir / f"goal-{stamp}.json"
    write_json(spec_path, spec)
    command = [sys.executable, str(ROOT / "scripts" / "run_ai_company_task_harness.py"), str(spec_path), "--mode", args.mode, "--out-root", str(out_root)]
    completed = subprocess.run(command, cwd=ROOT, env=os.environ.copy())
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
