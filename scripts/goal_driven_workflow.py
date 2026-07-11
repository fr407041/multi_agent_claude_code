from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.parse
from pathlib import Path
from typing import Any


CAPABILITIES = {"acquire", "analyze", "generate", "execute", "verify", "synthesize"}
TOOLS = {"none", "read", "write", "network", "python", "shell", "test"}
CRITERIA = {
    "artifact_exists",
    "json_valid",
    "json_path_equals",
    "numeric_compare",
    "command_success",
    "derived_provenance",
    "claim_evidence",
    "goal_answering",
    "non_empty",
    "json_min_items",
    "json_required_paths",
    "text_min_chars",
}
VERIFIER_REGISTRY = {
    "common_research": "scripts/verify_common_research_artifact.py",
    "sens_summary": "scripts/verify_sens_summary_artifact.py",
}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def safe_relative_path(value: str) -> bool:
    path = Path(str(value))
    return bool(value) and not path.is_absolute() and ".." not in path.parts


def _topological_order(jobs: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    ids = {str(job.get("id", "")) for job in jobs}
    dependencies = {str(job.get("id", "")): set(map(str, job.get("depends_on", []))) for job in jobs}
    order: list[str] = []
    ready = sorted(job_id for job_id, deps in dependencies.items() if not deps)
    while ready:
        current = ready.pop(0)
        order.append(current)
        for job_id in sorted(ids - set(order)):
            dependencies[job_id].discard(current)
            if not dependencies[job_id] and job_id not in ready:
                ready.append(job_id)
    return order, sorted(ids - set(order))


def validate_goal_plan(plan: dict[str, Any], max_jobs: int = 8) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    jobs = plan.get("jobs", []) if isinstance(plan.get("jobs"), list) else []
    if not jobs:
        errors.append({"code": "DAG_EMPTY", "detail": "goal plan must contain jobs"})
    if len(jobs) > max_jobs:
        errors.append({"code": "DAG_JOB_LIMIT", "detail": f"{len(jobs)} > {max_jobs}"})
    ids: list[str] = []
    producers: dict[str, str] = {}
    for index, job in enumerate(jobs):
        if not isinstance(job, dict):
            errors.append({"code": "DAG_JOB_INVALID", "detail": index})
            continue
        job_id = str(job.get("id", ""))
        if not job_id or job_id in ids:
            errors.append({"code": "DAG_JOB_ID_INVALID", "detail": job_id or index})
        ids.append(job_id)
        capability = str(job.get("capability", ""))
        if capability not in CAPABILITIES:
            errors.append({"code": "DAG_CAPABILITY_UNSUPPORTED", "detail": f"{job_id}:{capability}"})
        for tool in job.get("tools", []):
            if tool not in TOOLS:
                errors.append({"code": "DAG_TOOL_UNSUPPORTED", "detail": f"{job_id}:{tool}"})
        outputs = job.get("outputs", [])
        if not isinstance(outputs, list) or not outputs:
            errors.append({"code": "DAG_OUTPUTS_EMPTY", "detail": job_id})
        for output in outputs:
            if not safe_relative_path(str(output)):
                errors.append({"code": "DAG_PATH_UNSAFE", "detail": f"{job_id}:{output}"})
            elif output in producers:
                errors.append({"code": "DAG_DUPLICATE_PRODUCER", "detail": f"{output}:{producers[output]},{job_id}"})
            else:
                producers[str(output)] = job_id
        for path in job.get("inputs", []):
            if not safe_relative_path(str(path)):
                errors.append({"code": "DAG_PATH_UNSAFE", "detail": f"{job_id}:{path}"})
        criteria = job.get("acceptance_criteria", [])
        if not isinstance(criteria, list) or not criteria:
            errors.append({"code": "DAG_CRITERIA_EMPTY", "detail": job_id})
        for criterion in criteria:
            criterion_type = criterion.get("type") if isinstance(criterion, dict) else ""
            if criterion_type not in CRITERIA:
                errors.append({"code": "DAG_CRITERION_UNSUPPORTED", "detail": f"{job_id}:{criterion_type or criterion}"})
            elif criterion_type == "json_min_items" and (not isinstance(criterion.get("min_items"), int) or int(criterion.get("min_items", 0)) < 1):
                errors.append({"code": "DAG_CRITERION_INVALID", "detail": f"{job_id}:json_min_items"})
            elif criterion_type == "json_required_paths" and not criterion.get("json_paths"):
                errors.append({"code": "DAG_CRITERION_INVALID", "detail": f"{job_id}:json_required_paths"})
            elif criterion_type == "text_min_chars" and (not isinstance(criterion.get("min_chars"), int) or int(criterion.get("min_chars", 0)) < 1):
                errors.append({"code": "DAG_CRITERION_INVALID", "detail": f"{job_id}:text_min_chars"})
        if any(isinstance(item, dict) and item.get("type") == "command_success" for item in criteria) and not str(job.get("test_command", "")).strip():
            errors.append({"code": "DAG_TEST_COMMAND_MISSING", "detail": job_id})
        test_command = str(job.get("test_command", "")).strip()
        if test_command and (re.search(r"[;&|`$<>]", test_command) or not re.match(r"^(python3?|pytest)(?:\s|$)", test_command)):
            errors.append({"code": "DAG_TEST_COMMAND_UNSAFE", "detail": f"{job_id}:{test_command}"})
        source_url = str(job.get("source_url", "")).strip()
        if "network" in job.get("tools", []):
            parsed_url = urllib.parse.urlparse(source_url)
            allowed_hosts = {item.strip() for item in os.environ.get("AI_COMPANY_ALLOWED_NETWORK_HOSTS", "127.0.0.1,localhost").split(",") if item.strip()}
            if capability != "acquire":
                errors.append({"code": "DAG_NETWORK_CAPABILITY_INVALID", "detail": f"{job_id}:{capability}"})
            if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname or parsed_url.hostname not in allowed_hosts:
                errors.append({"code": "DAG_NETWORK_SOURCE_UNSAFE", "detail": f"{job_id}:{source_url or '<missing>'}"})
        covered_outputs = {
            str(item.get("path")) for item in criteria
            if isinstance(item, dict) and item.get("type") in {"artifact_exists", "json_valid", "json_path_equals", "numeric_compare", "derived_provenance", "non_empty", "json_min_items", "json_required_paths", "text_min_chars"}
        }
        for output in map(str, outputs):
            if output not in covered_outputs:
                errors.append({"code": "DAG_OUTPUT_CRITERION_MISSING", "detail": f"{job_id}:{output}"})
    id_set = set(ids)
    for job in jobs:
        if not isinstance(job, dict):
            continue
        job_id = str(job.get("id", ""))
        dependencies = set(map(str, job.get("depends_on", [])))
        for dep in dependencies:
            if dep not in id_set:
                errors.append({"code": "DAG_DEPENDENCY_MISSING", "detail": f"{job_id}:{dep}"})
        for input_path in map(str, job.get("inputs", [])):
            producer = producers.get(input_path)
            if producer and producer not in dependencies:
                errors.append({"code": "DAG_INPUT_DEPENDENCY_MISSING", "detail": f"{job_id}:{input_path}:{producer}"})
            if not producer and input_path not in set(map(str, plan.get("supplied_inputs", []))):
                errors.append({"code": "DAG_INPUT_PRODUCER_MISSING", "detail": f"{job_id}:{input_path}"})
    try:
        profiles = json.loads((Path(__file__).resolve().parents[1] / "configs/ai_company/agent_profiles.json").read_text(encoding="utf-8")).get("profiles", {})
    except (OSError, json.JSONDecodeError):
        profiles = {}
    for job in jobs:
        if not isinstance(job, dict):
            continue
        profile = profiles.get(str(job.get("agent_profile", "")), {}) if isinstance(profiles, dict) else {}
        limits = profile.get("scope_limits", {}) if isinstance(profile, dict) else {}
        max_files = int(limits.get("max_files", 0) or 0)
        if max_files and len(job.get("outputs", [])) > max_files:
            errors.append({"code": "PROFILE_SCOPE_LIMIT_EXCEEDED", "detail": f"{job.get('id')}:{len(job.get('outputs', []))}>{max_files}"})
        allowed_templates = limits.get("allowed_worker_templates", [])
        template = str(job.get("worker_template", ""))
        if allowed_templates and template not in allowed_templates and "auto" not in allowed_templates:
            errors.append({"code": "WORKER_PROFILE_MISMATCH", "detail": f"{job.get('id')}:{template}"})
    order, cyclic = _topological_order(jobs)
    if cyclic:
        errors.append({"code": "DAG_CYCLE", "detail": cyclic})
    return {
        "passed": not errors,
        "status": "pass" if not errors else "DAG_CONTRACT_INVALID",
        "job_count": len(jobs),
        "topological_order": order,
        "errors": errors,
        "model_calls_started": 0,
    }


def normalize_goal_plan(goal: str, payload: dict[str, Any], max_jobs: int, supplied_inputs: list[str]) -> dict[str, Any]:
    jobs: list[dict[str, Any]] = []
    raw_jobs = [item for item in payload.get("jobs", [])[:max_jobs] if isinstance(item, dict)]
    id_map = {str(raw.get("id") or f"job-{index:03d}"): f"job-{index:03d}" for index, raw in enumerate(raw_jobs, start=1)}
    for index, raw in enumerate(raw_jobs, start=1):
        if not isinstance(raw, dict):
            continue
        outputs = [str(item) for item in raw.get("outputs", [])][:2]
        capability = str(raw.get("capability", "analyze")).lower()
        owner = {
            "acquire": "research_agent", "analyze": "research_agent", "generate": "executor_backend",
            "execute": "executor_backend", "verify": "reviewer_worker", "synthesize": "synthesis_agent",
        }.get(capability, "research_agent")
        job_id = f"job-{index:03d}"
        criteria = []
        for item in raw.get("acceptance_criteria", []):
            if isinstance(item, dict) and item.get("type"):
                criteria.append(item)
            elif isinstance(item, dict) and len(item) == 1:
                key, value = next(iter(item.items()))
                if key in CRITERIA:
                    criteria.append({"type": key, "path": value} if isinstance(value, str) else {"type": key})
        if raw.get("evidence_requirements") and not any(item.get("type") == "claim_evidence" for item in criteria):
            criteria.append({"type": "claim_evidence"})
        jobs.append(
            {
                "id": job_id,
                "title": str(raw.get("title") or f"{capability.title()} bounded output"),
                "capability": capability,
                "instruction": str(raw.get("instruction") or f"Complete the {capability} step for this goal: {goal}"),
                "depends_on": [id_map.get(str(item), str(item)) for item in raw.get("depends_on", [])],
                "inputs": [str(item) for item in raw.get("inputs", [])],
                "outputs": outputs,
                "files": outputs,
                "tools": [str(item) for item in raw.get("tools", ["read", "write"])],
                "acceptance_criteria": criteria,
                "evidence_requirements": [str(item) for item in raw.get("evidence_requirements", ["claim_evidence"])],
                "success_check": "All declared acceptance criteria pass.",
                "owner_role": owner,
                "agent_profile": owner,
                "worker_template": "managed_single_file" if len(outputs) == 1 else "managed_multi_file",
                "require_change": capability != "verify" and bool(outputs),
                "fallback_plan": "Repair the earliest failed dependency and selectively rerun affected descendants.",
                "test_command": str(raw.get("test_command", "")),
                "source_url": str(raw.get("source_url", "")),
            }
        )
    redundant_ids = {
        str(job["id"]) for job in jobs
        if job.get("capability") == "acquire"
        and set(job.get("outputs", [])).issubset(set(supplied_inputs))
        and not job.get("source_url")
    }
    jobs = [job for job in jobs if job["id"] not in redundant_ids]
    for job in jobs:
        job["depends_on"] = [dep for dep in job.get("depends_on", []) if dep not in redundant_ids]
    consumed_outputs = {str(path) for job in jobs for path in job.get("inputs", [])}
    for job in jobs:
        criteria = job.get("acceptance_criteria", [])
        meaningful_paths = {
            str(item.get("path", "")) for item in criteria
            if isinstance(item, dict) and item.get("type") in {"non_empty", "json_min_items", "json_required_paths", "text_min_chars"}
        }
        for output in map(str, job.get("outputs", [])):
            if output in consumed_outputs and output not in meaningful_paths:
                criteria.append({"type": "non_empty", "path": output})
    if jobs and not any(item.get("type") == "goal_answering" for item in jobs[-1].get("acceptance_criteria", [])):
        jobs[-1]["acceptance_criteria"].append({"type": "goal_answering"})
    return {"goal": goal, "supplied_inputs": supplied_inputs, "max_jobs": max_jobs, "jobs": jobs}


def deterministic_goal_plan(goal: str, supplied_inputs: list[str]) -> dict[str, Any]:
    first_input = supplied_inputs[0] if supplied_inputs else "source.txt"
    return normalize_goal_plan(
        goal,
        {
            "jobs": [
                {
                    "id": "job-001", "capability": "analyze", "inputs": [first_input], "outputs": ["analysis.json"],
                    "tools": ["read", "write"], "acceptance_criteria": [{"type": "json_valid", "path": "analysis.json"}, {"type": "claim_evidence"}],
                },
                {
                    "id": "job-002", "capability": "synthesize", "depends_on": ["job-001"], "inputs": ["analysis.json"], "outputs": ["summary.md"],
                    "tools": ["read", "write"], "acceptance_criteria": [{"type": "artifact_exists", "path": "summary.md"}, {"type": "goal_answering"}],
                },
            ]
        },
        8,
        supplied_inputs or [first_input],
    )


def read_dot_path(payload: Any, path: str) -> tuple[bool, Any]:
    current = payload
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, current


def verify_job_contract(run_dir: Path, job: dict[str, Any], status: dict[str, Any], claims: list[dict[str, Any]]) -> dict[str, Any]:
    worktree = run_dir / "worktree"
    checks: list[dict[str, Any]] = []
    missing_artifacts: list[str] = []
    missing_inputs = [path for path in map(str, job.get("inputs", [])) if not (worktree / path).is_file()]
    insufficient_inputs: list[str] = []
    for rel in map(str, job.get("inputs", [])):
        source = worktree / rel
        if not source.is_file():
            continue
        try:
            text = source.read_text(encoding="utf-8", errors="ignore").strip()
            if not text:
                insufficient_inputs.append(rel)
            elif source.suffix.lower() == ".json":
                parsed = json.loads(text)
                if isinstance(parsed, (dict, list)) and not parsed:
                    insufficient_inputs.append(rel)
        except (OSError, json.JSONDecodeError):
            insufficient_inputs.append(rel)
    for criterion in job.get("acceptance_criteria", []):
        if not isinstance(criterion, dict):
            continue
        kind = str(criterion.get("type", ""))
        path = str(criterion.get("path", ""))
        target = worktree / path if path else None
        passed = False
        detail = ""
        try:
            if kind == "artifact_exists":
                passed = bool(target and target.is_file())
            elif kind == "json_valid":
                json.loads(target.read_text(encoding="utf-8"))
                passed = True
            elif kind == "json_path_equals":
                found, actual = read_dot_path(json.loads(target.read_text(encoding="utf-8")), str(criterion.get("json_path", "")))
                passed = found and actual == criterion.get("expected")
                detail = f"actual={actual!r}"
            elif kind == "numeric_compare":
                found, actual = read_dot_path(json.loads(target.read_text(encoding="utf-8")), str(criterion.get("json_path", "")))
                expected = criterion.get("value")
                operator = criterion.get("operator")
                passed = found and isinstance(actual, (int, float)) and isinstance(expected, (int, float)) and {
                    ">": actual > expected, ">=": actual >= expected, "<": actual < expected, "<=": actual <= expected, "==": actual == expected,
                }.get(operator, False)
                detail = f"actual={actual!r} operator={operator} expected={expected!r}"
            elif kind == "command_success":
                passed = int(status.get("test_exit_code", 1)) == 0 and bool(status.get("test_executed_command"))
            elif kind == "derived_provenance":
                passed = path in status.get("actual_changed_files", []) and int(status.get("actual_changed_count", 0)) > 0
            elif kind == "claim_evidence":
                passed = bool(claims) and all(item.get("evidence_refs") for item in claims)
            elif kind == "goal_answering":
                passed = bool(claims) and any(len(str(item.get("claim", "")).strip()) >= 20 for item in claims)
            elif kind == "non_empty":
                content = target.read_text(encoding="utf-8", errors="ignore").strip()
                if target.suffix.lower() == ".json":
                    parsed = json.loads(content)
                    passed = bool(parsed) if isinstance(parsed, (dict, list, str)) else parsed is not None
                else:
                    passed = bool(content)
            elif kind == "json_min_items":
                parsed = json.loads(target.read_text(encoding="utf-8"))
                found, value = read_dot_path(parsed, str(criterion.get("json_path", ""))) if criterion.get("json_path") else (True, parsed)
                passed = found and isinstance(value, (dict, list)) and len(value) >= int(criterion.get("min_items", 1))
            elif kind == "json_required_paths":
                parsed = json.loads(target.read_text(encoding="utf-8"))
                passed = all(read_dot_path(parsed, str(required))[0] for required in criterion.get("json_paths", []))
            elif kind == "text_min_chars":
                passed = len(target.read_text(encoding="utf-8", errors="ignore").strip()) >= int(criterion.get("min_chars", 1))
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            detail = str(exc)
            passed = False
        if path and not passed and (not target or not target.is_file()):
            missing_artifacts.append(path)
        checks.append({"type": kind, "path": path, "status": "passed" if passed else "failed", "detail": detail})
    process_ok = status.get("status") == "SUCCESS" and int(status.get("exit_code", 0)) == 0
    if not process_ok:
        checks.append({"type": "process_success", "path": "", "status": "failed", "detail": status.get("verification_note", "")})
    failed = [item for item in checks if item["status"] == "failed"]
    failure_family = str(status.get("failure_family", "")).lower()
    if failure_family == "external_dependency":
        category = "EXTERNAL_DEPENDENCY_FAILED"
    elif missing_inputs or insufficient_inputs:
        category = "INPUT_INSUFFICIENT"
    else:
        category = "ARTIFACT_CONTRACT_FAILED" if failed else ""
    return {
        "job_id": job.get("id"), "all_passed": not failed and not missing_inputs and not insufficient_inputs, "checks": checks,
        "failed_checks": failed, "missing_inputs": sorted(set(missing_inputs + insufficient_inputs)), "insufficient_inputs": insufficient_inputs, "missing_artifacts": sorted(set(missing_artifacts)),
        "failure_category": category,
    }


def recovery_fingerprint(
    job: dict[str, Any],
    missing_inputs: list[str],
    failed_checks: list[dict[str, Any]],
    instruction: str,
    worktree: Path | None = None,
) -> str:
    stable_checks = [
        {"type": item.get("type", ""), "path": item.get("path", ""), "status": item.get("status", "")}
        for item in failed_checks if isinstance(item, dict)
    ]
    input_hashes: dict[str, str] = {}
    if worktree is not None:
        for rel in map(str, job.get("inputs", [])):
            path = worktree / rel
            input_hashes[rel] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "<missing>"
    payload = {
        "job_id": job.get("id"), "inputs": job.get("inputs", []), "input_hashes": input_hashes,
        "outputs": job.get("outputs", []), "criteria": job.get("acceptance_criteria", []),
        "worker_template": job.get("worker_template", ""), "agent_profile": job.get("agent_profile", ""),
        "profile_mode": job.get("profile_mode", ""), "missing_inputs": sorted(missing_inputs),
        "failed_checks": stable_checks, "instruction": instruction,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def descendants(jobs: list[dict[str, Any]], root_id: str) -> list[str]:
    affected: set[str] = set()
    changed = True
    while changed:
        changed = False
        for job in jobs:
            job_id = str(job.get("id"))
            if job_id == root_id or job_id in affected:
                continue
            deps = set(map(str, job.get("depends_on", [])))
            if root_id in deps or deps & affected:
                affected.add(job_id)
                changed = True
    return sorted(affected)


def build_final_verdict(
    run_id: str,
    dag_report: dict[str, Any],
    generic_report: dict[str, Any],
    recovery_events: list[dict[str, Any]],
) -> dict[str, Any]:
    failed_jobs = [item for item in generic_report.get("jobs", []) if not item.get("all_passed")]
    root = failed_jobs[0] if failed_jobs else {}
    blocked = generic_report.get("blocked_descendants", [])
    accepted_count = sum(1 for item in generic_report.get("jobs", []) if item.get("all_passed"))
    status = "pass" if dag_report.get("passed") and not failed_jobs and not blocked else "partial" if accepted_count else "fail"
    return {
        "run_id": run_id, "overall_status": status, "root_failed_job": root.get("job_id", ""),
        "failure_category": root.get("failure_category", ""), "failed_checks": root.get("failed_checks", []),
        "missing_inputs": root.get("missing_inputs", []), "missing_artifacts": root.get("missing_artifacts", []),
        "missing_evidence": [item.get("path", "") for item in root.get("failed_checks", []) if item.get("type") == "claim_evidence"],
        "blocked_descendants": blocked, "recovery_action": recovery_events[-1].get("action", "") if recovery_events else "",
        "next_action": "Deliver result" if status == "pass" else (root.get("recommended_recovery_job") and f"Repair {root['recommended_recovery_job']}" or "Inspect root failure and recovery trace"),
        "accepted_job_count": accepted_count,
        "source": "canonical_final_run_verdict",
    }
