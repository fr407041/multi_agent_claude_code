from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
PROFILES_PATH = ROOT / "configs" / "ai_company" / "agent_profiles.json"
PROFILE_STATUS_PROFILE_VIOLATION = "PROFILE_POLICY_VIOLATION"
PROFILE_STATUS_DOWNGRADED = "PROFILE_DOWNGRADED"
PROFILE_STATUS_ENFORCEMENT_UNAVAILABLE = "PROFILE_ENFORCEMENT_UNAVAILABLE"


def read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except (json.JSONDecodeError, OSError):
        return {}


def load_agent_profiles(path: Path = PROFILES_PATH) -> dict[str, Any]:
    registry = read_json(path)
    registry.setdefault("profiles", {})
    return registry


def normalize_profile_mode(value: Any) -> str:
    mode = str(value or "auto").strip().lower()
    if mode not in {"auto", "simple", "strict"}:
        return "auto"
    return mode


def strict_trigger_reasons(spec: dict[str, Any], meeting: dict[str, Any] | None = None) -> list[str]:
    reasons: list[str] = []
    jobs = spec.get("jobs", []) if isinstance(spec.get("jobs"), list) else []
    task_type = str(spec.get("task_type", "")).lower()
    if len(jobs) > 1:
        reasons.append("more_than_one_execution_role")
    if any(str(job.get("mock_status", "")).upper() in {"OVERFLOW_DETECTED", "ROUTER_ERROR", "NEEDS_REPLAN"} for job in jobs):
        reasons.append("seeded_failure_pressure")
    if task_type in {"sensitive", "security", "finance", "compliance"}:
        reasons.append("sensitive_task_type")
    expectations = spec.get("expectations", {}) if isinstance(spec.get("expectations"), dict) else {}
    if expectations.get("require_no_reviewer_quality_gap") is True:
        reasons.append("false_success_sensitive")
    if meeting:
        risks = " ".join(str(item).lower() for item in meeting.get("open_risks", []))
        if "overflow" in risks:
            reasons.append("historical_overflow_pressure")
        if "router" in risks:
            reasons.append("router_instability_pressure")
        if "false success" in risks or "false-success" in risks:
            reasons.append("repeated_false_success_pressure")
    return sorted(set(reasons))


def resolve_run_profile_mode(spec: dict[str, Any], meeting: dict[str, Any] | None = None) -> tuple[str, list[str]]:
    requested = normalize_profile_mode(spec.get("agent_profile_mode", "auto"))
    if requested in {"simple", "strict"}:
        return requested, [f"requested_{requested}"]
    reasons = strict_trigger_reasons(spec, meeting)
    return ("strict" if reasons else "simple", reasons or ["auto_default_simple"])


def profile_for_role(role: str, registry: dict[str, Any] | None = None) -> dict[str, Any]:
    registry = registry or load_agent_profiles()
    profiles = registry.get("profiles", {})
    return dict(profiles.get(role) or profiles.get("research_agent") or {})


def resolve_profile(
    owner_role: str,
    requested_profile: str | None = None,
    requested_mode: str = "auto",
    registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    registry = registry or load_agent_profiles()
    profiles = registry.get("profiles", {})
    profile_id = str(requested_profile or owner_role or "research_agent").strip()
    profile = dict(profiles.get(profile_id) or profiles.get(owner_role) or profiles.get("research_agent") or {})
    profile.setdefault("profile_id", profile_id)
    profile.setdefault("display_name", profile_id)
    profile.setdefault("allowed_skills", [])
    profile.setdefault("allowed_mcp_groups", [])
    profile.setdefault("tool_policy", "limited")
    profile.setdefault("scope_limits", {})
    profile.setdefault("instruction_contract", {})
    profile.setdefault("recovery_policy", {})

    mode = normalize_profile_mode(requested_mode)
    effective_mode = profile.get("mode", "simple") if mode == "auto" else mode
    if effective_mode not in {"simple", "strict"}:
        effective_mode = "simple"
    policy_level = "advisory + verified-posthoc" if effective_mode == "simple" else "enforced-by-wrapper + verified-posthoc"
    profile["profile_mode"] = effective_mode
    profile["effective_policy_level"] = policy_level
    profile["resolved_from"] = "manual" if requested_profile else "auto-assigned from role"
    return profile


def apply_profile_to_job(job: dict[str, Any], run_profile_mode: str, registry: dict[str, Any] | None = None) -> dict[str, Any]:
    owner_role = str(job.get("owner_role", "research_agent"))
    profile = resolve_profile(owner_role, job.get("agent_profile"), run_profile_mode, registry)
    enriched = dict(job)
    enriched["agent_profile"] = profile.get("profile_id", owner_role)
    enriched["profile_mode"] = profile.get("profile_mode", "simple")
    enriched["effective_policy_level"] = profile.get("effective_policy_level", "advisory + verified-posthoc")
    enriched["effective_allowed_skills"] = profile.get("allowed_skills", [])
    enriched["effective_allowed_mcp_groups"] = profile.get("allowed_mcp_groups", [])
    enriched["effective_tool_policy"] = profile.get("tool_policy", "limited")
    enriched["profile_resolved_from"] = profile.get("resolved_from", "auto-assigned from role")
    enriched["profile_settings_overlay"] = profile.get("settings_overlay", "")
    enriched["profile_role_prompt"] = profile.get("instruction_contract", {}).get("role_prompt", "")
    enriched["profile_contract"] = profile.get("instruction_contract", {})
    enriched["profile_scope_limits"] = profile.get("scope_limits", {})
    enriched["profile_recovery_policy"] = profile.get("recovery_policy", {})
    return enriched


def profile_prompt_note(job: dict[str, Any]) -> str:
    role_prompt = str(job.get("profile_role_prompt", "")).strip()
    contract = job.get("profile_contract", {}) if isinstance(job.get("profile_contract"), dict) else {}
    forbidden = contract.get("forbidden_behaviors", [])
    expected = contract.get("expected_output_contract", "")
    return "\n".join(
        [
            "AGENT_PROFILE_ACTIVE",
            f"profile_id: {job.get('agent_profile', '')}",
            f"profile_mode: {job.get('profile_mode', '')}",
            f"policy_level: {job.get('effective_policy_level', '')}",
            f"role_prompt_file: {role_prompt}",
            f"allowed_skills: {job.get('effective_allowed_skills', [])}",
            f"allowed_mcp_groups: {job.get('effective_allowed_mcp_groups', [])}",
            f"tool_policy: {job.get('effective_tool_policy', '')}",
            f"expected_output_contract: {expected}",
            f"forbidden_behaviors: {forbidden}",
            "Return concise summary, key_claims, evidence_refs, confidence, limitations, and handoff_next.",
        ]
    )


def profile_policy_issues(job_or_status: dict[str, Any], enforce_missing: bool = False) -> list[str]:
    issues: list[str] = []
    profile_fields = {"agent_profile", "profile_mode", "effective_policy_level", "profile_scope_limits"}
    has_profile_metadata = any(key in job_or_status for key in profile_fields)
    if not has_profile_metadata and not enforce_missing:
        return issues
    if not job_or_status.get("agent_profile"):
        issues.append("missing_agent_profile")
    if not job_or_status.get("profile_mode"):
        issues.append("missing_profile_mode")
    if not job_or_status.get("effective_policy_level"):
        issues.append("missing_effective_policy_level")
    mode = str(job_or_status.get("profile_mode", "")).lower()
    if mode == "strict" and "enforced-by-wrapper" not in str(job_or_status.get("effective_policy_level", "")):
        issues.append("strict_profile_not_wrapper_enforced")
    limits = job_or_status.get("profile_scope_limits", {})
    files = job_or_status.get("files", [])
    if isinstance(limits, dict) and limits.get("max_files") is not None:
        try:
            if len(files or []) > int(limits.get("max_files", 999)):
                issues.append("scope_exceeds_profile_max_files")
        except (TypeError, ValueError):
            pass
    return issues


def build_profile_summary(run_dir: Path) -> dict[str, Any]:
    rows = []
    violations = []
    for job_path in sorted((run_dir / "jobs").glob("*.json")):
        job = read_json(job_path)
        issues = profile_policy_issues(job)
        row = {
            "task_id": job.get("id", job_path.stem),
            "owner_role": job.get("owner_role", ""),
            "agent_profile": job.get("agent_profile", ""),
            "profile_mode": job.get("profile_mode", ""),
            "effective_policy_level": job.get("effective_policy_level", ""),
            "effective_tool_policy": job.get("effective_tool_policy", ""),
            "resolved_from": job.get("profile_resolved_from", ""),
            "policy_issues": issues,
        }
        rows.append(row)
        for issue in issues:
            violations.append({"task_id": row["task_id"], "owner_role": row["owner_role"], "issue": issue})
    modes = {str(row.get("profile_mode", "")) for row in rows if row.get("profile_mode")}
    return {
        "run_profile_mode": "strict" if "strict" in modes else "simple" if "simple" in modes else "unknown",
        "agent_profiles": rows,
        "policy_violations": violations,
        "downgrade_summary": [],
    }
