from __future__ import annotations

import argparse
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


def live_goal_plan(goal: str, supplied_inputs: list[str], max_jobs: int, artifact_dir: Path, response_file: str = "") -> tuple[dict[str, Any], dict[str, Any]]:
    if response_file:
        source = Path(response_file).resolve()
        text = source.read_text(encoding="utf-8")
        return normalize_goal_plan(goal, extract_json_object(text), max_jobs, supplied_inputs), {
            "mode": "live_resume", "transport": "recorded_provider_response", "transport_reason": "resume_after_deterministic_validation",
            "provider_usage": {}, "raw_artifact": str(source),
        }
    transport, reason = resolve_live_meeting_transport()
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
Use only relative safe paths. Every non-supplied input must have exactly one upstream producer.
Keep each job to at most two outputs. End with a synthesize job that answers the goal.
"""
    result = call_live_provider_for_turn(transport, "planner_agent", {"goal": goal}, prompt, artifact_dir)
    raw_file = artifact_dir / "goal_planner.raw.txt"
    raw_file.parent.mkdir(parents=True, exist_ok=True)
    raw_file.write_text(result.text, encoding="utf-8")
    payload = extract_json_object(result.text)
    metadata = {
        "mode": "live", "transport": transport, "transport_reason": reason,
        "provider_usage": result.provider_usage, "raw_artifact": str(raw_file),
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
    if args.mode == "live":
        plan, planner_metadata = live_goal_plan(args.goal, args.supplied_input, args.max_jobs, artifact_dir, args.planner_response_file)
    else:
        plan = deterministic_goal_plan(args.goal, args.supplied_input)
        planner_metadata = {"mode": "mock", "transport": "deterministic", "raw_artifact": ""}
    dag_report = validate_goal_plan(plan, args.max_jobs)
    write_json(artifact_dir / "goal_plan.json", plan)
    write_json(artifact_dir / "dag_validation_report.json", dag_report)
    if not dag_report["passed"]:
        print(json.dumps({"goal_plan": plan, "dag_validation": dag_report, "planner": planner_metadata}, ensure_ascii=False, indent=2))
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
        "goal_planner": planner_metadata,
        "expectations": {"meeting_status": "MEETING_READY", "loop_bounded": True, "max_execution_jobs_run": args.max_jobs + args.max_replans},
    }
    spec_path = generated_dir / f"goal-{stamp}.json"
    write_json(spec_path, spec)
    command = [sys.executable, str(ROOT / "scripts" / "run_ai_company_task_harness.py"), str(spec_path), "--mode", args.mode, "--out-root", str(out_root)]
    completed = subprocess.run(command, cwd=ROOT, env=os.environ.copy())
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
