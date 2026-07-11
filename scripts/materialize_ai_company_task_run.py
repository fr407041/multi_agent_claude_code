from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from agent_profile_resolver import apply_profile_to_job, load_agent_profiles, resolve_run_profile_mode


ROOT = Path(__file__).resolve().parent.parent
RUN_ROOT = ROOT / "results" / "ai_company_task_runs"
DEFAULTS_PATH = ROOT / "configs" / "ai_company" / "task_harness.defaults.json"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def build_run_id(spec_id: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    return f"run-{stamp}-{spec_id}"


def materialize_run(spec_path: Path, out_root: Path | None = None) -> Path:
    spec = read_json(spec_path)
    defaults = read_json(DEFAULTS_PATH) if DEFAULTS_PATH.exists() else {}
    spec.setdefault("agent_profile_mode", defaults.get("agent_profile_mode", "auto"))
    run_root = out_root or RUN_ROOT
    run_root.mkdir(parents=True, exist_ok=True)

    run_id = build_run_id(spec["id"])
    run_dir = run_root / run_id
    jobs_dir = run_dir / "jobs"
    results_dir = run_dir / "results"
    ai_company_dir = run_dir / "ai_company"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)
    ai_company_dir.mkdir(parents=True, exist_ok=True)

    workspace_root = Path(os.environ.get("AI_COMPANY_WORKSPACE_ROOT", str(ROOT))).resolve()
    try:
        spec_path.resolve().relative_to(ROOT.resolve())
        scope_root = ROOT
    except ValueError:
        scope_root = workspace_root
    scope_subdir = str(spec.get("scope_subdir", "."))
    goal_driven = (spec.get("workflow") or {}).get("mode") == "goal_driven"
    scope_path = run_dir / "worktree" if goal_driven or spec.get("scope_copy_from") else (scope_root / scope_subdir).resolve()
    if spec.get("scope_copy_from"):
        source_scope = (scope_root / str(spec["scope_copy_from"])).resolve()
        shutil.copytree(source_scope, scope_path)
    elif goal_driven:
        scope_path.mkdir(parents=True, exist_ok=True)
        source_scope = (scope_root / scope_subdir).resolve()
        for supplied in map(str, (spec.get("goal_plan") or {}).get("supplied_inputs", [])):
            source = (source_scope / supplied).resolve()
            try:
                source.relative_to(source_scope)
            except ValueError as exc:
                raise ValueError(f"supplied input escapes source scope: {supplied}") from exc
            if source.is_file():
                target = (scope_path / supplied).resolve()
                target.relative_to(scope_path.resolve())
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
    jobs = spec.get("jobs", []) or ((spec.get("goal_plan") or {}).get("jobs", []) if (spec.get("workflow") or {}).get("mode") == "goal_driven" else [])
    run_profile_mode, profile_reasons = resolve_run_profile_mode(spec)
    profile_registry = load_agent_profiles()

    plan = {
        "strategy": spec.get("strategy", "Use bounded planning before dispatch."),
        "agent_profile_mode": run_profile_mode,
        "profile_resolution_reasons": profile_reasons,
        "jobs": jobs,
    }
    write_json(run_dir / "plan.json", plan)
    if spec.get("workflow", {}).get("mode") == "goal_driven":
        write_json(ai_company_dir / "goal_plan.json", spec.get("goal_plan", {"goal": spec.get("goal"), "jobs": jobs}))
        write_json(ai_company_dir / "dag_validation_report.json", spec.get("dag_validation_report", {}))
        write_json(ai_company_dir / "goal_planner.json", spec.get("goal_planner", {}))

    for job in jobs:
        payload = dict(job)
        payload["scope_path"] = str(scope_path)
        payload.setdefault("require_change", False)
        payload.setdefault("test_command", "")
        payload = apply_profile_to_job(payload, run_profile_mode, profile_registry)
        write_json(jobs_dir / f"{payload['id']}.json", payload)

    summary = {
        "run_id": run_id,
        "task": spec["goal"],
        "scope_path": str(scope_path),
        "strategy": spec.get("strategy", "Use bounded planning before dispatch."),
        "worker_mode": "task_harness",
        "agent_profile_mode": run_profile_mode,
        "profile_resolution_reasons": profile_reasons,
        "planner_exit": 0,
        "planner_parse_ok": True,
        "plan_json_file": str((run_dir / "plan.json").resolve()),
        "jobs_dir": str(jobs_dir.resolve()),
        "results_dir": str(results_dir.resolve()),
        "spec_file": str(spec_path.resolve()),
        "expectations": spec.get("expectations", {}),
        "workflow": spec.get("workflow", {"mode": "fixed"}),
        "verification": spec.get("verification", {}),
        "recovery": spec.get("recovery", {}),
        "metrics": {
            "total_files_in_scope": sum(len(job.get("files", [])) for job in jobs),
            "compact_inventory_files": sum(len(job.get("files", [])) for job in jobs),
            "initial_planned_jobs": len(jobs),
            "planned_jobs_after_retries": len(jobs),
            "avg_files_per_job": f"{(sum(len(job.get('files', [])) for job in jobs) / len(jobs)):.2f}" if jobs else "0.00",
            "max_files_in_job": max((len(job.get("files", [])) for job in jobs), default=0),
        },
    }
    write_json(run_dir / "summary.json", summary)
    return run_dir


def main() -> None:
    import sys

    if len(sys.argv) not in {2, 3}:
        raise SystemExit("Usage: materialize_ai_company_task_run.py <spec_path> [out_root]")
    spec_path = Path(sys.argv[1]).resolve()
    out_root = Path(sys.argv[2]).resolve() if len(sys.argv) == 3 else None
    run_dir = materialize_run(spec_path, out_root)
    print(json.dumps({"run_dir": str(run_dir)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
