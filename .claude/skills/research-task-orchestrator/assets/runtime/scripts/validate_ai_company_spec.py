from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Any

from goal_driven_workflow import VERIFIER_REGISTRY, validate_goal_plan


ROOT = Path(__file__).resolve().parent.parent
ALLOWED_WORKER_TEMPLATES = {
    "summary_markdown",
    "managed_single_file",
    "managed_multi_file",
    "bounded_worker",
    "inspection_only",
}


def referenced_script(command: str, root: Path) -> Path | None:
    rendered = command.replace("{root}", root.as_posix()).replace("{scope_path}", "/scope").replace("{run_dir}", "/run")
    try:
        argv = shlex.split(rendered, posix=True)
    except ValueError:
        return None
    if len(argv) >= 2 and Path(argv[0]).name in {"python", "python3"}:
        return Path(argv[1])
    if argv and ("/" in argv[0] or "\\" in argv[0]):
        return Path(argv[0])
    return None


def validate_spec(spec_path: Path, root: Path = ROOT) -> dict[str, Any]:
    spec_path = spec_path.resolve()
    errors: list[dict[str, str]] = []
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "passed": False,
            "spec_contract_status": "SPEC_CONTRACT_INVALID",
            "artifact_contract_status": "unknown",
            "worker_template_status": "unknown",
            "referenced_command_status": "unknown",
            "errors": [{"code": "SPEC_JSON_INVALID", "detail": str(exc)}],
        }

    if not isinstance(spec, dict):
        errors.append({"code": "SPEC_ROOT_INVALID", "detail": "spec root must be an object"})
        spec = {}
    if (spec.get("workflow") or {}).get("mode") == "goal_driven" and not spec.get("jobs"):
        spec["jobs"] = (spec.get("goal_plan") or {}).get("jobs", [])
    for key in ["id", "goal", "jobs"]:
        if not spec.get(key):
            errors.append({"code": "SPEC_FIELD_MISSING", "detail": key})

    jobs = spec.get("jobs", []) if isinstance(spec.get("jobs"), list) else []
    workflow = spec.get("workflow", {}) if isinstance(spec.get("workflow"), dict) else {}
    workflow_mode = str(workflow.get("mode", "fixed"))
    if workflow_mode not in {"fixed", "goal_driven"}:
        errors.append({"code": "WORKFLOW_MODE_INVALID", "detail": workflow_mode})
    if workflow_mode == "goal_driven":
        verification = spec.get("verification", {}) if isinstance(spec.get("verification"), dict) else {}
        if verification.get("type") != "generic_contract":
            errors.append({"code": "VERIFIER_SCOPE_MISMATCH", "detail": "goal_driven workflow requires generic_contract"})
        dag_report = validate_goal_plan(
            {"goal": spec.get("goal"), "jobs": jobs, "supplied_inputs": (spec.get("goal_plan") or {}).get("supplied_inputs", [])},
            int(workflow.get("max_jobs", 8)),
        )
        errors.extend(dag_report.get("errors", []))
    verification = spec.get("verification", {}) if isinstance(spec.get("verification"), dict) else {}
    verification_type = str(verification.get("type", "legacy_explicit_command" if spec.get("post_verify_command") else "generic_contract"))
    if verification_type not in {"generic_contract", "fixture", "legacy_explicit_command"}:
        errors.append({"code": "VERIFIER_TYPE_INVALID", "detail": verification_type})
    if verification_type == "fixture":
        verifier_id = str(verification.get("verifier_id", ""))
        if verifier_id not in VERIFIER_REGISTRY:
            errors.append({"code": "VERIFIER_SCOPE_MISMATCH", "detail": verifier_id or "fixture verifier_id missing"})
        expected_script = VERIFIER_REGISTRY.get(verifier_id, "")
        if expected_script and expected_script not in str(spec.get("post_verify_command", "")):
            errors.append({"code": "VERIFIER_SCOPE_MISMATCH", "detail": f"{verifier_id} must explicitly select {expected_script}"})
    seen_ids: set[str] = set()
    output_owners: dict[str, list[str]] = {}
    try:
        profile_registry = json.loads((root / "configs/ai_company/agent_profiles.json").read_text(encoding="utf-8")).get("profiles", {})
    except (OSError, json.JSONDecodeError):
        profile_registry = {}
    worker_errors = 0
    for index, job in enumerate(jobs):
        if not isinstance(job, dict):
            errors.append({"code": "JOB_INVALID", "detail": f"jobs[{index}] must be an object"})
            continue
        job_id = str(job.get("id", ""))
        if not job_id:
            errors.append({"code": "JOB_FIELD_MISSING", "detail": f"jobs[{index}].id"})
        elif job_id in seen_ids:
            errors.append({"code": "JOB_ID_DUPLICATE", "detail": job_id})
        seen_ids.add(job_id)
        for key in ["instruction", "files", "success_check", "owner_role", "agent_profile"]:
            if not job.get(key):
                errors.append({"code": "JOB_FIELD_MISSING", "detail": f"{job_id or index}.{key}"})
        files = job.get("files", []) if isinstance(job.get("files"), list) else []
        if not files:
            errors.append({"code": "JOB_FILES_EMPTY", "detail": job_id or str(index)})
        for rel in files:
            output_owners.setdefault(str(rel), []).append(job_id)
        template = str(job.get("worker_template", "")).strip().lower()
        if template not in ALLOWED_WORKER_TEMPLATES:
            worker_errors += 1
            errors.append(
                {
                    "code": "WORKER_TEMPLATE_INVALID",
                    "detail": f"{job_id or index}: {template or '<missing>'}",
                }
            )
        if bool(job.get("require_change")) and template == "inspection_only":
            worker_errors += 1
            errors.append({"code": "WORKER_TEMPLATE_CONFLICT", "detail": f"{job_id}: inspection_only cannot require change"})
        output_count = len(job.get("outputs", files) if isinstance(job.get("outputs", files), list) else [])
        if template == "managed_single_file" and output_count != 1:
            worker_errors += 1
            errors.append({"code": "WORKER_OUTPUT_COUNT_MISMATCH", "detail": f"{job_id}: managed_single_file requires exactly one output"})
        if template == "managed_multi_file" and output_count != 2:
            worker_errors += 1
            errors.append({"code": "WORKER_OUTPUT_COUNT_MISMATCH", "detail": f"{job_id}: managed_multi_file requires exactly two outputs"})
        if template == "bounded_worker" and bool(job.get("require_change")):
            worker_errors += 1
            errors.append({"code": "WORKER_TEMPLATE_CONFLICT", "detail": f"{job_id}: bounded_worker cannot own required output changes"})
        profile_id = str(job.get("agent_profile", ""))
        profile = profile_registry.get(profile_id, {}) if isinstance(profile_registry, dict) else {}
        allowed_templates = (profile.get("scope_limits") or {}).get("allowed_worker_templates", []) if isinstance(profile, dict) else []
        max_files = int((profile.get("scope_limits") or {}).get("max_files", 0) or 0) if isinstance(profile, dict) else 0
        if allowed_templates and template not in allowed_templates and "auto" not in allowed_templates:
            worker_errors += 1
            errors.append({"code": "WORKER_PROFILE_MISMATCH", "detail": f"{job_id}: {profile_id} does not allow {template}"})
        if max_files and len(files) > max_files:
            worker_errors += 1
            errors.append({"code": "PROFILE_SCOPE_LIMIT_EXCEEDED", "detail": f"{job_id}: {len(files)} > {max_files}"})

    command_errors = 0
    for field in ["prep_command", "post_verify_command"]:
        command = str(spec.get(field, "")).strip()
        if not command:
            continue
        script = referenced_script(command, root)
        if script is None or not script.is_file():
            command_errors += 1
            errors.append({"code": "REFERENCED_COMMAND_MISSING", "detail": f"{field}: {script or command}"})

    workspace_root = Path(os.environ.get("AI_COMPANY_WORKSPACE_ROOT", str(root))).resolve()
    try:
        spec_path.relative_to(root.resolve())
        source_base = root.resolve()
    except ValueError:
        source_base = workspace_root
    scope_source = str(spec.get("scope_copy_from", "")).strip()
    scope_root = (source_base / scope_source).resolve() if scope_source else source_base
    if scope_source and not scope_root.exists():
        errors.append({"code": "SCOPE_SOURCE_MISSING", "detail": scope_source})

    artifact_contract_errors = 0
    requirements_path = scope_root / "artifact_requirements.json"
    requires_summary = bool(spec.get("post_verify_command")) or requirements_path.is_file()
    if requires_summary:
        owners = output_owners.get("summary.md", [])
        if len(owners) != 1:
            artifact_contract_errors += 1
            errors.append(
                {
                    "code": "ARTIFACT_CONTRACT_INCOMPLETE",
                    "detail": f"summary.md must have exactly one owner; found {len(owners)}",
                }
            )
    if requirements_path.is_file():
        try:
            requirements = json.loads(requirements_path.read_text(encoding="utf-8"))
            if not isinstance(requirements, dict) or not requirements:
                raise ValueError("artifact requirements must be a non-empty object")
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            artifact_contract_errors += 1
            errors.append({"code": "ARTIFACT_REQUIREMENTS_INVALID", "detail": str(exc)})

    passed = not errors
    return {
        "passed": passed,
        "spec_file": str(spec_path),
        "spec_id": spec.get("id", ""),
        "spec_contract_status": "pass" if passed else "SPEC_CONTRACT_INVALID",
        "artifact_contract_status": "pass" if artifact_contract_errors == 0 else "ARTIFACT_CONTRACT_INCOMPLETE",
        "worker_template_status": "pass" if worker_errors == 0 else "invalid",
        "referenced_command_status": "pass" if command_errors == 0 else "missing",
        "job_count": len(jobs),
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate an ai-company task spec before any model call.")
    parser.add_argument("spec")
    parser.add_argument("--root", default=str(ROOT))
    args = parser.parse_args()
    report = validate_spec(Path(args.spec), Path(args.root).resolve())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
