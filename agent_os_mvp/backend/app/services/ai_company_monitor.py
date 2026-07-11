from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from sqlite3 import Connection
from typing import Any


def get_project_root() -> Path:
    configured = os.environ.get("AI_COMPANY_PROJECT_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()

    resolved = Path(__file__).resolve()
    # Source checkout: <project>/agent_os_mvp/backend/app/services/...
    if len(resolved.parents) > 4:
        return resolved.parents[4]
    # Container/runtime fallback: keep all relative paths under the app root.
    return resolved.parents[-1]


REPO_ROOT = get_project_root()


def get_results_root() -> Path:
    configured = os.environ.get("AI_COMPANY_RESULTS_ROOT", "").strip()
    if not configured:
        return REPO_ROOT / "results" / "ai_company_task_harness"
    path = Path(configured).expanduser()
    return path.resolve() if path.is_absolute() else (REPO_ROOT / path).resolve()

CANONICAL_ROSTER = [
    "meeting_coordinator",
    "planner_agent",
    "risk_reviewer",
    "decision_agent",
    "synthesis_agent",
    "reviewer_worker",
]

FAILURE_FAMILY_ORDER = [
    "overflow",
    "router",
    "timeout",
    "replan",
    "repair",
    "false_success_blocked",
    "profile_policy_violation",
    "generic_failed",
]

TERMINAL_SUCCESS_VERDICTS = {"ACCEPTED", "COMPLETE"}
TERMINAL_FAILURE_VERDICTS = {"FALSE_SUCCESS_BLOCKED", "REPLAN_REQUIRED", "REPAIR_REQUIRED", "PROFILE_POLICY_VIOLATION"}
NON_TERMINAL_STATES = {"QUEUED", "PENDING", "RUNNING", "IN_PROGRESS", "REVIEW_PENDING"}
PRIMARY_RUN_STATUSES = {"pass", "fail", "partial"}


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore").strip()


def _parse_run_started_at(run_name: str) -> str | None:
    match = re.match(r"run-(\d{8})-(\d{6})-", run_name)
    if not match:
        return None
    return datetime.strptime("".join(match.groups()), "%Y%m%d%H%M%S").isoformat()


def _shorten(text: str, limit: int = 220) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."


def _run_started_at(run_dir: Path, report: dict[str, Any]) -> str:
    explicit = str(report.get("started_at") or "").strip()
    if explicit:
        return explicit
    parsed = _parse_run_started_at(run_dir.name)
    if parsed:
        return parsed
    candidates = [run_dir / "ai_company" / "task_harness_report.json", run_dir / "task.txt", run_dir]
    existing = [path for path in candidates if path.exists()]
    timestamp = max(path.stat().st_mtime for path in existing) if existing else run_dir.stat().st_mtime
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def _normalize_evidence_ref(value: Any) -> dict[str, str] | None:
    if isinstance(value, str) and value.strip():
        return {"type": "file", "path": value.strip()}
    if isinstance(value, dict):
        path = str(value.get("path") or value.get("file") or value.get("source") or "").strip()
        if path:
            return {"type": str(value.get("type") or "file"), "path": path}
    return None


def _normalize_claims(claims: list[Any]) -> list[dict[str, Any]]:
    normalized = []
    for index, claim in enumerate(claims):
        if not isinstance(claim, dict):
            continue
        refs = [item for raw in claim.get("evidence_refs", []) if (item := _normalize_evidence_ref(raw))]
        normalized.append({**claim, "claim_id": claim.get("claim_id") or f"claim-{index + 1}", "evidence_refs": refs})
    return normalized


def _severity_from_alert_type(alert_type: str) -> str:
    if alert_type in {"overflow", "router_error", "replan_loop"}:
        return "red"
    if alert_type in {"timeout", "artifact_regression"}:
        return "yellow"
    return "green"


def _build_alerts(
    report: dict[str, Any],
    watchdog: dict[str, Any] | None = None,
    artifact_verify: dict[str, Any] | None = None,
    domain_verdict: dict[str, Any] | None = None,
    provider_diagnostic: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    kpis = report.get("kpis", {})
    failure_counts = kpis.get("failure_family_counts", {})
    alerts: list[dict[str, Any]] = []
    watchdog = watchdog or {}
    artifact_verify = artifact_verify or {}
    domain_verdict = domain_verdict or {}
    provider_diagnostic = provider_diagnostic or {}
    if str(report.get("overall_status", "")).lower() == "fail":
        alerts.append({"type": "run_failed", "severity": "red", "title": "Run Failed", "detail": report.get("error") or "The run did not satisfy its execution or verification contract."})
    if str(watchdog.get("watchdog_status", "")).lower() == "escalated":
        alerts.append({"type": "watchdog_escalated", "severity": "red", "title": "Watchdog Escalated", "detail": watchdog.get("last_action") or "Watchdog requires operator attention."})
    if kpis.get("package_integrity") not in {None, "", "pass"}:
        alerts.append(
            {
                "type": "package_integrity",
                "severity": "red",
                "title": "Package Integrity Failed",
                "detail": "Runtime, spec, or verifier files do not match one release. Fix package integrity before rerunning agents.",
            }
        )
    if domain_verdict.get("enabled") and domain_verdict.get("status") != "pass":
        first_defect = (domain_verdict.get("defects") or [{}])[0]
        location = ":".join(filter(None, [str(first_defect.get("artifact", "")), str(first_defect.get("path", ""))]))
        alerts.append(
            {
                "type": "domain_verdict_failed",
                "severity": "red",
                "title": "Domain Result Failed",
                "detail": f"{location or 'Domain contract'}: {first_defect.get('reason') or first_defect.get('kind') or 'runner-owned validation failed'}",
            }
        )

    if failure_counts.get("overflow", 0) > 0:
        alerts.append(
            {
                "type": "overflow",
                "severity": _severity_from_alert_type("overflow"),
                "title": "Token Overflow",
                "detail": f"Overflow count = {failure_counts.get('overflow', 0)}. Narrow child scope before rerun.",
            }
        )
    if failure_counts.get("router", 0) > 0:
        alerts.append(
            {
                "type": "router_error",
                "severity": _severity_from_alert_type("router_error"),
                "title": "Router Error",
                "detail": f"Router error count = {failure_counts.get('router', 0)}. Check model endpoint or partial response handling.",
            }
        )
    if kpis.get("replan_required_count", 0) > 0:
        alerts.append(
            {
                "type": "replan_loop",
                "severity": _severity_from_alert_type("replan_loop"),
                "title": "Replan Pressure",
                "detail": f"Replan required count = {kpis.get('replan_required_count', 0)}. Current task slicing may still be too broad.",
            }
        )
    if provider_diagnostic.get("classification") == "MODEL_VISIBLE_BUT_COMPLETION_TIMEOUT":
        alerts.append(
            {
                "type": "provider_diagnostic_timeout",
                "severity": "yellow",
                "title": "Provider Diagnostic Timeout",
                "detail": provider_diagnostic.get("next_action") or "Direct provider completion timed out; continue with router live gates.",
            }
        )
    if failure_counts.get("timeout", 0) > 0:
        alerts.append(
            {
                "type": "timeout",
                "severity": _severity_from_alert_type("timeout"),
                "title": "Timeout",
                "detail": f"Timeout count = {failure_counts.get('timeout', 0)}. Consider reducing context or increasing timeout budget.",
            }
        )

    artifact = kpis.get("artifact_verify", {}).get("parsed", {}) or artifact_verify
    if artifact and not artifact.get("all_passed", True):
        failed_checks = [key for key, value in artifact.get("checks", {}).items() if not value]
        alerts.append(
            {
                "type": "artifact_regression",
                "severity": _severity_from_alert_type("artifact_regression"),
                "title": "Artifact Check Failed",
                "detail": f"Failed checks: {', '.join(failed_checks) if failed_checks else 'unknown'}",
            }
        )

    if not alerts and str(report.get("overall_status", "unknown")).lower() == "pass" and domain_verdict.get("status") != "fail":
        alerts.append(
            {
                "type": "healthy",
                "severity": "green",
                "title": "Healthy",
                "detail": "No overflow, router error, or replan signal in this run.",
            }
        )
    return alerts


def _read_optional_text(path: Path, limit: int = 12000) -> str:
    if not path or str(path) == "." or not path.exists() or not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="ignore")
    if len(text) <= limit:
        return text
    return text[:limit] + "\n[TRUNCATED]\n"


def _workspace_path_to_local(value: str) -> Path:
    if not value:
        return Path()
    return Path(value.replace("/workspace", str(REPO_ROOT).replace("\\", "/")))


def _failure_summary_template() -> dict[str, int]:
    return {key: 0 for key in FAILURE_FAMILY_ORDER}


def _normalize_overall_status(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in PRIMARY_RUN_STATUSES:
        return normalized
    return "unknown"


def _derive_failure_family(status: dict[str, Any] | None, verdict: dict[str, Any] | None) -> str | None:
    if verdict:
        verdict_name = str(verdict.get("verdict", "")).upper()
        if verdict_name == "FALSE_SUCCESS_BLOCKED":
            return "false_success_blocked"
        if verdict_name == "REPLAN_REQUIRED":
            return "replan"
        if verdict_name == "REPAIR_REQUIRED":
            return "repair"
        if verdict_name in {"FAILED", "REJECTED"}:
            return "generic_failed"
        if verdict_name in {"PROFILE_POLICY_VIOLATION", "PROFILE_DOWNGRADED", "PROFILE_ENFORCEMENT_UNAVAILABLE"}:
            return "profile_policy_violation"

    raw_status = str((status or {}).get("status", "")).upper()
    if raw_status in {"PROFILE_POLICY_VIOLATION", "PROFILE_DOWNGRADED", "PROFILE_ENFORCEMENT_UNAVAILABLE"}:
        return "profile_policy_violation"
    if "OVERFLOW" in raw_status:
        return "overflow"
    if "ROUTER" in raw_status:
        return "router"
    if "TIMEOUT" in raw_status:
        return "timeout"
    if raw_status in {"NEEDS_REPLAN", "REPLAN_REQUIRED"}:
        return "replan"
    if raw_status == "REPAIR_REQUIRED":
        return "repair"
    if raw_status in {"FAILED", "ERROR"}:
        return "generic_failed"
    return None


def _is_terminal_upstream_result(
    dependency_task_id: str,
    status_by_task: dict[str, dict[str, Any]],
    verdict_by_task: dict[str, dict[str, Any]],
) -> bool:
    verdict = verdict_by_task.get(dependency_task_id)
    if verdict:
        verdict_name = str(verdict.get("verdict", "")).upper()
        if verdict_name in TERMINAL_SUCCESS_VERDICTS | TERMINAL_FAILURE_VERDICTS | {"FAILED", "REJECTED"}:
            return True

    status = status_by_task.get(dependency_task_id)
    if status:
        raw_status = str(status.get("status", "")).upper()
        if raw_status and raw_status not in NON_TERMINAL_STATES:
            return True
    return False


def _review_assignment_collapsed_to_upstream_verdict(
    role: str,
    assignment: dict[str, Any] | None,
    status_by_task: dict[str, dict[str, Any]],
    verdict_by_task: dict[str, dict[str, Any]],
) -> bool:
    if role != "reviewer_worker" or assignment is None:
        return False
    dependency_ids = assignment.get("depends_on") or []
    if not dependency_ids:
        return False
    return all(_is_terminal_upstream_result(task_id, status_by_task, verdict_by_task) for task_id in dependency_ids)


def _derive_dashboard_state(
    role: str,
    assignment: dict[str, Any] | None,
    status: dict[str, Any] | None,
    verdict: dict[str, Any] | None,
    participated_in_meeting: bool,
    status_by_task: dict[str, dict[str, Any]],
    verdict_by_task: dict[str, dict[str, Any]],
) -> str:
    if assignment is None:
        return "done" if participated_in_meeting else "idle"

    failure_family = _derive_failure_family(status, verdict)
    if failure_family:
        return "failed"

    verdict_name = str((verdict or {}).get("verdict", "")).upper()
    if verdict_name in TERMINAL_SUCCESS_VERDICTS:
        return "done"

    raw_status = str((status or {}).get("status", "")).upper()
    if raw_status in NON_TERMINAL_STATES:
        return "waiting" if assignment.get("depends_on") else "running"

    if _review_assignment_collapsed_to_upstream_verdict(role, assignment, status_by_task, verdict_by_task):
        dependency_ids = assignment.get("depends_on") or []
        dependency_families = [
            _derive_failure_family(status_by_task.get(task_id), verdict_by_task.get(task_id))
            for task_id in dependency_ids
        ]
        if any(dependency_families):
            return "failed"
        return "done"

    if raw_status == "SUCCESS":
        return "waiting"

    if assignment.get("depends_on"):
        return "waiting"

    return "running"


def _build_failure_record(
    role: str,
    assignment: dict[str, Any],
    status: dict[str, Any] | None,
    verdict: dict[str, Any] | None,
    status_by_task: dict[str, dict[str, Any]],
    verdict_by_task: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    failure_family = _derive_failure_family(status, verdict)
    if not failure_family and _review_assignment_collapsed_to_upstream_verdict(role, assignment, status_by_task, verdict_by_task):
        dependency_ids = assignment.get("depends_on") or []
        dependency_failure_records = [
            (
                task_id,
                _derive_failure_family(status_by_task.get(task_id), verdict_by_task.get(task_id)),
                status_by_task.get(task_id),
                verdict_by_task.get(task_id),
            )
            for task_id in dependency_ids
        ]
        first_failure = next((item for item in dependency_failure_records if item[1]), None)
        if first_failure:
            dependency_task_id, failure_family, upstream_status, upstream_verdict = first_failure
            return {
                "task_id": assignment.get("task_id", ""),
                "role": role,
                "phase": "review",
                "failure_family": failure_family,
                "status": (upstream_status or {}).get("status", "UPSTREAM_VERDICT"),
                "verdict": (upstream_verdict or {}).get("verdict", ""),
                "verification_note": f"Derived from upstream task {dependency_task_id}",
                "detected_by": (upstream_status or {}).get("detected_by", ""),
                "failure_reason": (upstream_status or {}).get("failure_reason", ""),
                "recommended_next_action": (upstream_status or {}).get("recommended_next_action", ""),
                "fallback_plan": assignment.get("fallback_plan", ""),
                "scope": assignment.get("scope", []),
            }

    if not failure_family:
        return None

    return {
        "task_id": assignment.get("task_id", ""),
        "role": role,
        "phase": "review" if role == "reviewer_worker" else "execution",
        "failure_family": failure_family,
        "status": (status or {}).get("status", "UNKNOWN"),
        "verdict": (verdict or {}).get("verdict", ""),
        "verification_note": (status or {}).get("verification_note", ""),
        "detected_by": (status or {}).get("detected_by", ""),
        "failure_reason": (status or {}).get("failure_reason", ""),
        "recommended_next_action": (status or {}).get("recommended_next_action", ""),
        "fallback_plan": assignment.get("fallback_plan", ""),
        "scope": assignment.get("scope", []),
    }


def _build_agent_state_board(
    meeting: dict[str, Any],
    status_by_task: dict[str, dict[str, Any]],
    verdict_by_task: dict[str, dict[str, Any]],
    token_by_agent: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[str], dict[str, list[dict[str, Any]]], list[dict[str, Any]], dict[str, int]]:
    token_by_agent = token_by_agent or {}
    discussion_roles = [item.get("role", "") for item in meeting.get("discussion_log", []) if item.get("role")]
    assignment_roles = [item.get("owner_role", "") for item in meeting.get("task_assignments", []) if item.get("owner_role")]

    roster: list[str] = []
    for role in CANONICAL_ROSTER + discussion_roles + assignment_roles:
        if role and role not in roster:
            roster.append(role)

    assignments_by_role: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for assignment in meeting.get("task_assignments", []):
        assignments_by_role[assignment.get("owner_role", "unknown")].append(assignment)

    participated_roles = set(discussion_roles)
    board = {state: [] for state in ["running", "waiting", "done", "failed", "idle"]}
    failures_by_agent: list[dict[str, Any]] = []
    failure_summary = _failure_summary_template()

    for role in roster:
        assignments = assignments_by_role.get(role, [])
        state_records = []
        failure_records = []

        if not assignments:
            state = _derive_dashboard_state(
                role,
                None,
                None,
                None,
                role in participated_roles,
                status_by_task,
                verdict_by_task,
            )
            state_records.append(
                {
                    "role": role,
                    "state": state,
                    "task_id": "",
                    "phase": "meeting" if role in participated_roles else "idle",
                    "scope": [],
                    "depends_on": [],
                    "fallback_plan": "",
                    "verification_note": "",
                    "token_usage_summary": token_by_agent.get(role, {}),
                }
            )
        else:
            for assignment in assignments:
                task_id = assignment.get("task_id", "")
                status = status_by_task.get(task_id)
                verdict = verdict_by_task.get(task_id)
                state = _derive_dashboard_state(
                    role,
                    assignment,
                    status,
                    verdict,
                    role in participated_roles,
                    status_by_task,
                    verdict_by_task,
                )
                state_records.append(
                    {
                        "role": role,
                        "state": state,
                        "task_id": task_id,
                        "phase": "review" if role == "reviewer_worker" else "execution",
                        "scope": assignment.get("scope", []),
                        "depends_on": assignment.get("depends_on", []),
                        "fallback_plan": assignment.get("fallback_plan", ""),
                        "verification_note": (status or {}).get("verification_note", ""),
                        "detected_by": (status or {}).get("detected_by", ""),
                        "failure_reason": (status or {}).get("failure_reason", ""),
                        "recommended_next_action": (status or {}).get("recommended_next_action", ""),
                        "status": (status or {}).get("status", ""),
                        "verdict": (verdict or {}).get("verdict", ""),
                        "agent_profile": assignment.get("agent_profile") or (status or {}).get("agent_profile", ""),
                        "profile_mode": assignment.get("profile_mode") or (status or {}).get("profile_mode", ""),
                        "effective_policy_level": assignment.get("effective_policy_level") or (status or {}).get("effective_policy_level", ""),
                        "effective_tool_policy": assignment.get("effective_tool_policy") or (status or {}).get("effective_tool_policy", ""),
                        "profile_resolved_from": assignment.get("profile_resolved_from", ""),
                        "profile_policy_notes": (verdict or {}).get("profile_policy_notes", []),
                        "token_usage_summary": token_by_agent.get(role, {}),
                    }
                )
                failure_record = _build_failure_record(
                    role,
                    assignment,
                    status,
                    verdict,
                    status_by_task,
                    verdict_by_task,
                )
                if failure_record:
                    failure_records.append(failure_record)
                    failure_summary[failure_record["failure_family"]] += 1

        precedence = {"failed": 4, "running": 3, "waiting": 2, "done": 1, "idle": 0}
        selected = max(state_records, key=lambda item: precedence[item["state"]])
        board[selected["state"]].append(selected)

        if failure_records:
            failures_by_agent.append(
                {
                    "role": role,
                    "failure_count": len(failure_records),
                    "failures": failure_records,
                }
            )

    return roster, board, failures_by_agent, failure_summary


def _build_recent_agent_failure_trend(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    trend: dict[str, dict[str, Any]] = {}
    for run in runs:
        run_id = run.get("run_id", "")
        for agent_row in run.get("failures_by_agent", []):
            role = agent_row.get("role", "unknown")
            entry = trend.setdefault(
                role,
                {
                    "role": role,
                    "failed_runs": 0,
                    "failure_count": 0,
                    "failure_summary": _failure_summary_template(),
                    "last_failed_run_id": None,
                },
            )
            entry["failed_runs"] += 1
            entry["last_failed_run_id"] = entry["last_failed_run_id"] or run_id
            for failure in agent_row.get("failures", []):
                family = failure.get("failure_family", "generic_failed")
                entry["failure_count"] += 1
                entry["failure_summary"][family] = entry["failure_summary"].get(family, 0) + 1

    rows = []
    for role, data in trend.items():
        most_common_failure_family = "none"
        if data["failure_count"] > 0:
            most_common_failure_family = max(
                data["failure_summary"].items(),
                key=lambda item: (item[1], item[0]),
            )[0]
        rows.append(
            {
                **data,
                "most_common_failure_family": most_common_failure_family,
            }
        )
    return sorted(rows, key=lambda item: (-item["failure_count"], item["role"]))


def _build_agent_reliability_snapshot(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    snapshot: dict[str, dict[str, Any]] = {}
    for run in runs:
        run_id = run.get("run_id", "")
        role_states = {
            item.get("role"): state_name
            for state_name, items in run.get("agent_state_board", {}).items()
            for item in items
        }
        for role in run.get("roster", []):
            entry = snapshot.setdefault(
                role,
                {
                    "role": role,
                    "total_assignments": 0,
                    "success_count": 0,
                    "fail_count": 0,
                    "failure_summary": _failure_summary_template(),
                    "last_failed_run_id": None,
                },
            )
            state = role_states.get(role, "idle")
            if state != "idle":
                entry["total_assignments"] += 1
            if state == "done":
                entry["success_count"] += 1
            if state == "failed":
                entry["fail_count"] += 1
                entry["last_failed_run_id"] = entry["last_failed_run_id"] or run_id

        for agent_row in run.get("failures_by_agent", []):
            role = agent_row.get("role", "unknown")
            entry = snapshot.setdefault(
                role,
                {
                    "role": role,
                    "total_assignments": 0,
                    "success_count": 0,
                    "fail_count": 0,
                    "failure_summary": _failure_summary_template(),
                    "last_failed_run_id": run_id,
                },
            )
            for failure in agent_row.get("failures", []):
                family = failure.get("failure_family", "generic_failed")
                entry["failure_summary"][family] = entry["failure_summary"].get(family, 0) + 1

    rows = []
    for role, data in snapshot.items():
        most_common_failure_family = "none"
        if sum(data["failure_summary"].values()) > 0:
            most_common_failure_family = max(
                data["failure_summary"].items(),
                key=lambda item: (item[1], item[0]),
            )[0]
        rows.append(
            {
                **data,
                "most_common_failure_family": most_common_failure_family,
            }
        )
    return sorted(rows, key=lambda item: (-item["fail_count"], item["role"]))


def _load_recent_run_payloads(connection: Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT payload_json, alerts_json
        FROM ai_company_runs
        ORDER BY started_at DESC, run_id DESC
        LIMIT 12
        """
    ).fetchall()
    payloads = []
    for row in rows:
        payload = json.loads(row["payload_json"])
        payload["alerts"] = json.loads(row["alerts_json"])
        payloads.append(payload)
    return payloads


def _build_selected_run_summary(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not payload:
        return None

    board = payload.get("agent_state_board", {})
    roster = payload.get("roster", [])
    roster_count = payload.get("roster_count", len(roster))

    return {
        "run_id": payload.get("run_id", ""),
        "overall_status": _normalize_overall_status(payload.get("overall_status")),
        "roster_count": roster_count,
        "done_agent_count": len(board.get("done", [])),
        "waiting_agent_count": len(board.get("waiting", [])),
        "running_agent_count": len(board.get("running", [])),
        "failed_agent_count": len(board.get("failed", [])),
        "idle_agent_count": len(board.get("idle", [])),
    }


def _load_all_run_rows(connection: Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT run_id, overall_status, started_at
        FROM ai_company_runs
        ORDER BY started_at DESC, run_id DESC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _build_all_runs_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts = Counter(_normalize_overall_status(row.get("overall_status")) for row in rows)
    unknown_runs = [
        {
            "run_id": row.get("run_id", ""),
            "started_at": row.get("started_at"),
            "stored_status": row.get("overall_status", ""),
        }
        for row in rows
        if _normalize_overall_status(row.get("overall_status")) == "unknown"
    ]

    return {
        "total_runs": len(rows),
        "pass_count": status_counts.get("pass", 0),
        "fail_count": status_counts.get("fail", 0) + status_counts.get("partial", 0),
        "unknown_count": status_counts.get("unknown", 0),
        "status_breakdown": {
            "pass": status_counts.get("pass", 0),
            "fail": status_counts.get("fail", 0),
            "partial": status_counts.get("partial", 0),
            "unknown": status_counts.get("unknown", 0),
        },
        "unknown_runs": unknown_runs,
    }


def _summarize_run(run_dir: Path) -> dict[str, Any]:
    ai_dir = run_dir / "ai_company"
    report = _load_json(ai_dir / "task_harness_report.json") if (ai_dir / "task_harness_report.json").exists() else {}
    meeting = _load_json(ai_dir / "meeting_decision.json") if (ai_dir / "meeting_decision.json").exists() else {}
    execution = _load_json(ai_dir / "execution_summary.json") if (ai_dir / "execution_summary.json").exists() else {}
    reviewer = _load_json(ai_dir / "reviewer_verdicts.json") if (ai_dir / "reviewer_verdicts.json").exists() else {}
    claim_ledger = _load_json(ai_dir / "subagent_claim_ledger.json") if (ai_dir / "subagent_claim_ledger.json").exists() else {}
    token_ledger = _load_json(ai_dir / "agent_token_ledger.json") if (ai_dir / "agent_token_ledger.json").exists() else {}
    package_preflight = _load_json(ai_dir / "package_preflight_report.json") if (ai_dir / "package_preflight_report.json").exists() else {}
    watchdog = _load_json(ai_dir / "watchdog_report.json") if (ai_dir / "watchdog_report.json").exists() else {}
    provider_diagnostic = _load_json(ai_dir / "provider_diagnostic_report.json") if (ai_dir / "provider_diagnostic_report.json").exists() else {}
    if not provider_diagnostic and (run_dir / "provider_diagnostic_report.json").exists():
        provider_diagnostic = _load_json(run_dir / "provider_diagnostic_report.json")
    domain_verdict = _load_json(ai_dir / "domain_verdict.json") if (ai_dir / "domain_verdict.json").exists() else {}
    final_run_verdict = _load_json(ai_dir / "final_run_verdict.json") if (ai_dir / "final_run_verdict.json").exists() else {}
    goal_plan = _load_json(ai_dir / "goal_plan.json") if (ai_dir / "goal_plan.json").exists() else {}
    dag_validation = _load_json(ai_dir / "dag_validation_report.json") if (ai_dir / "dag_validation_report.json").exists() else {}
    dependency_state = _load_json(ai_dir / "dependency_state.json") if (ai_dir / "dependency_state.json").exists() else {}
    generic_contract = _load_json(ai_dir / "generic_contract_report.json") if (ai_dir / "generic_contract_report.json").exists() else {}
    recovery_trace = []
    recovery_path = ai_dir / "recovery_trace.jsonl"
    if recovery_path.is_file():
        for line in recovery_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                recovery_trace.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    plan = _load_json(run_dir / "plan.json") if (run_dir / "plan.json").exists() else {}
    summary = _read_text(run_dir / "worktree" / "summary.md") if (run_dir / "worktree" / "summary.md").exists() else ""

    status_records: list[dict[str, Any]] = []
    for status_path in sorted((run_dir / "results").glob("*.status.json")):
        status_records.append(_load_json(status_path))

    status_by_task = {item.get("id"): item for item in status_records}
    verdict_by_task = {item.get("task_id"): item for item in reviewer.get("verdicts", []) if item.get("task_id")}

    token_by_agent = {item.get("agent"): item for item in token_ledger.get("agents", []) if item.get("agent")}

    roster, agent_state_board, failures_by_agent, failure_summary = _build_agent_state_board(
        meeting,
        status_by_task,
        verdict_by_task,
        token_by_agent,
    )

    discussion = [
        {
            "round": item.get("round"),
            "role": item.get("role"),
            "summary": item.get("summary", ""),
            "decision_state": item.get("decision_state", ""),
            "proposed_actions": item.get("proposed_actions", []),
        }
        for item in meeting.get("discussion_log", [])
    ]

    artifact_verify = report.get("kpis", {}).get("artifact_verify", {}).get("parsed", {})
    if not artifact_verify and (ai_dir / "artifact_verify_report.json").exists():
        artifact_verify = _load_json(ai_dir / "artifact_verify_report.json").get("parsed", {})
    alerts = _build_alerts(report, watchdog, artifact_verify, domain_verdict, provider_diagnostic)
    effective_overall_status = final_run_verdict.get("overall_status") or ("fail" if domain_verdict.get("enabled") and domain_verdict.get("status") != "pass" else report.get("overall_status", "unknown"))
    normalized_claims = _normalize_claims(claim_ledger.get("claims", []))

    status_details = []
    for item in status_records:
        raw_path = _workspace_path_to_local(str(item.get("raw_file", "")))
        prompt_path = raw_path.with_name(raw_path.name.replace(".raw.txt", ".prompt.txt")) if raw_path.name.endswith(".raw.txt") else Path()
        exec_log_path = _workspace_path_to_local(str(item.get("exec_log_file", "")))
        test_path = _workspace_path_to_local(str(item.get("test_output_file", "")))
        status_details.append(
            {
                "id": item.get("id", ""),
                "status": item.get("status", "UNKNOWN"),
                "owner_role": item.get("owner_role", ""),
                "agent_profile": item.get("agent_profile", ""),
                "profile_mode": item.get("profile_mode", ""),
                "effective_policy_level": item.get("effective_policy_level", ""),
                "effective_tool_policy": item.get("effective_tool_policy", ""),
                "profile_policy_issues": item.get("profile_policy_issues", []),
                "verification_note": item.get("verification_note", ""),
                "raw_excerpt": _read_optional_text(raw_path),
                "prompt_excerpt": _read_optional_text(prompt_path),
                "exec_log_excerpt": _read_optional_text(exec_log_path),
                "test_output_excerpt": _read_optional_text(test_path),
            }
        )

    final_result = {
        "summary_markdown": summary,
        "summary_excerpt": _shorten(summary, 420),
        "artifact_score": artifact_verify.get("score"),
        "artifact_checks": artifact_verify.get("checks", {}),
        "all_passed": artifact_verify.get("all_passed"),
        "semantic_expectations": artifact_verify.get("semantic_expectations", []),
        "semantic_expectations_passed": artifact_verify.get("semantic_expectations_passed"),
        "schema_context": artifact_verify.get("schema_context", {}),
        "domain_verdict": domain_verdict,
        "provider_diagnostic": provider_diagnostic,
        "final_run_verdict": final_run_verdict,
        "generic_contract": generic_contract,
        "accepted_count": report.get("kpis", {}).get("accepted_count", 0),
        "overall_status": effective_overall_status,
    }

    current_status = {
        "run_id": run_dir.name,
        "spec_id": report.get("spec_id", ""),
        "overall_status": effective_overall_status,
        "meeting_status": meeting.get("meeting_status", ""),
        "meeting_mode": meeting.get("meeting_mode", "deterministic"),
        "live_meeting_used": bool(meeting.get("live_meeting_used", False)),
        "live_transport": meeting.get("live_transport", ""),
        "live_transport_reason": meeting.get("live_transport_reason", ""),
        "live_degraded": bool(meeting.get("live_degraded", False)),
        "degrade_reason": meeting.get("degrade_reason", ""),
        "degrade_category": meeting.get("degrade_category", ""),
        "package_integrity": package_preflight.get("package_integrity", report.get("kpis", {}).get("package_integrity", "unknown")),
        "release_id": package_preflight.get("release_id", report.get("kpis", {}).get("release_id", "")),
        "spec_contract_status": package_preflight.get("spec_contract_status", report.get("kpis", {}).get("spec_contract_status", "unknown")),
        "started_at": _run_started_at(run_dir, report),
        "goal": meeting.get("goal") or report.get("kpis", {}).get("goal", ""),
        "decision_summary": meeting.get("decision_summary", ""),
        "convergence_reason": meeting.get("convergence_reason", ""),
        "alerts": alerts,
        "execution_jobs_run": report.get("kpis", {}).get("execution_jobs_run", 0),
        "replan_required_count": report.get("kpis", {}).get("replan_required_count", 0),
        "failed_agent_count": len(agent_state_board["failed"]),
        "running_agent_count": len(agent_state_board["running"]),
        "waiting_agent_count": len(agent_state_board["waiting"]),
        "done_agent_count": len(agent_state_board["done"]),
        "idle_agent_count": len(agent_state_board["idle"]),
        "roster_count": len(roster),
        "run_profile_mode": report.get("kpis", {}).get("run_profile_mode") or meeting.get("run_profile_mode", "unknown"),
        "total_estimated_agent_tokens": report.get("kpis", {}).get("total_estimated_agent_tokens", token_ledger.get("total_estimated_agent_tokens", 0)),
        "max_agent_turn_tokens": report.get("kpis", {}).get("max_agent_turn_tokens", token_ledger.get("max_agent_turn_tokens", 0)),
        "top_token_agent": report.get("kpis", {}).get("top_token_agent", token_ledger.get("top_token_agent", "")),
        "overflow_risk_agent_count": report.get("kpis", {}).get("overflow_risk_agent_count", token_ledger.get("overflow_risk_agent_count", 0)),
    }

    active_agents = agent_state_board["running"] + agent_state_board["waiting"]

    return {
        **current_status,
        "roster": roster,
        "active_agents": active_agents,
        "agent_state_board": agent_state_board,
        "agent_activity": [
            item
            for state_name in ["running", "waiting", "done", "failed", "idle"]
            for item in agent_state_board[state_name]
        ],
        "failures_by_agent": failures_by_agent,
        "failure_summary": failure_summary,
        "run_profile_mode": current_status["run_profile_mode"],
        "agent_profiles": report.get("kpis", {}).get("agent_profiles", []),
        "policy_violations": report.get("kpis", {}).get("policy_violations", []),
        "downgrade_summary": report.get("kpis", {}).get("downgrade_summary", []),
        "claim_ledger": {
            "metrics": claim_ledger.get("metrics", {}),
            "claims": normalized_claims[:50],
        },
        "claim_ledger_metrics": claim_ledger.get("metrics", {}),
        "token_ledger": token_ledger,
        "package_preflight": package_preflight,
        "token_summary": {
            "warning_threshold_tokens": token_ledger.get("warning_threshold_tokens"),
            "total_estimated_agent_tokens": current_status["total_estimated_agent_tokens"],
            "total_provider_agent_tokens": token_ledger.get("total_provider_agent_tokens", 0),
            "total_effective_agent_tokens": token_ledger.get("total_effective_agent_tokens", 0),
            "max_agent_turn_tokens": current_status["max_agent_turn_tokens"],
            "top_token_agent": current_status["top_token_agent"],
            "overflow_risk_agent_count": current_status["overflow_risk_agent_count"],
        },
        "watchdog": watchdog,
        "provider_diagnostic": provider_diagnostic,
        "meeting": {
            "meeting_mode": meeting.get("meeting_mode", "deterministic"),
            "live_meeting_used": bool(meeting.get("live_meeting_used", False)),
            "live_turn_count": meeting.get("live_turn_count", 0),
            "live_transport": meeting.get("live_transport", ""),
            "live_transport_reason": meeting.get("live_transport_reason", ""),
            "live_degraded": bool(meeting.get("live_degraded", False)),
            "degrade_reason": meeting.get("degrade_reason", ""),
            "degrade_category": meeting.get("degrade_category", ""),
            "transcript_file": meeting.get("transcript_file", ""),
            "rounds_used": meeting.get("rounds_used", 0),
            "round_limit": meeting.get("round_limit", 0),
            "constraints": meeting.get("constraints", []),
            "open_risks": meeting.get("open_risks", []),
            "discussion_log": discussion,
            "task_assignments": meeting.get("task_assignments", []),
            "plan_strategy": plan.get("strategy", ""),
            "plan_jobs": plan.get("jobs", []),
        },
        "review_verdicts": reviewer.get("verdicts", []),
        "execution_log": execution.get("execution_log", []),
        "status_details": status_details,
        "final_result": final_result,
        "goal_dag": {"plan": goal_plan, "validation": dag_validation, "dependency_state": dependency_state},
        "recovery_trace": recovery_trace,
        "final_run_verdict": final_run_verdict,
        "kpis": report.get("kpis", {}),
    }


def sync_ai_company_runs(connection: Connection) -> None:
    results_root = get_results_root()
    # The configured artifact root is the dashboard's source of truth. Clearing
    # stale rows prevents runs from a previous mount from leaking into All runs.
    connection.execute("DELETE FROM ai_company_runs")
    if not results_root.exists():
        return
    run_dirs = [
        path
        for path in results_root.iterdir()
        if path.is_dir() and (path / "ai_company" / "task_harness_report.json").is_file()
    ]
    synced_at = datetime.now(timezone.utc).isoformat()
    for run_dir in run_dirs:
        try:
            payload = _summarize_run(run_dir)
        except Exception:
            continue
        connection.execute(
            """
            INSERT INTO ai_company_runs (
                run_id, spec_id, mode, overall_status, started_at, goal, decision_summary,
                meeting_status, artifact_score, active_agent_count, alerts_json, payload_json, synced_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                spec_id = excluded.spec_id,
                mode = excluded.mode,
                overall_status = excluded.overall_status,
                started_at = excluded.started_at,
                goal = excluded.goal,
                decision_summary = excluded.decision_summary,
                meeting_status = excluded.meeting_status,
                artifact_score = excluded.artifact_score,
                active_agent_count = excluded.active_agent_count,
                alerts_json = excluded.alerts_json,
                payload_json = excluded.payload_json,
                synced_at = excluded.synced_at
            """,
            (
                payload["run_id"],
                payload.get("spec_id", ""),
                payload.get("mode", ""),
                payload.get("overall_status", "unknown"),
                payload.get("started_at"),
                payload.get("goal", ""),
                payload.get("decision_summary", ""),
                payload.get("meeting_status", ""),
                payload.get("final_result", {}).get("artifact_score"),
                len(payload.get("active_agents", [])),
                json.dumps(payload.get("alerts", []), ensure_ascii=False),
                json.dumps(payload, ensure_ascii=False),
                synced_at,
            ),
        )
    connection.commit()


def collect_ai_company_monitor(connection: Connection) -> dict[str, Any]:
    sync_ai_company_runs(connection)
    all_rows = _load_all_run_rows(connection)
    all_runs_summary = _build_all_runs_summary(all_rows)
    runs = _load_recent_run_payloads(connection)
    spec_counts = Counter(item.get("spec_id", "unknown") for item in runs if item.get("spec_id"))
    latest_run = runs[0] if runs else None
    selected_run_preview = _build_selected_run_summary(latest_run)

    return {
        "overview": {
            "total_runs": all_runs_summary["total_runs"],
            "pass_count": all_runs_summary["pass_count"],
            "fail_count": all_runs_summary["fail_count"],
            "unknown_count": all_runs_summary["unknown_count"],
            "status_breakdown": all_runs_summary["status_breakdown"],
            "specs": [{"spec_id": key, "count": value} for key, value in spec_counts.most_common()],
            "active_agents": len(latest_run.get("active_agents", [])) if latest_run else 0,
            "latest_status": latest_run.get("overall_status") if latest_run else "unknown",
            "running_agent_count": selected_run_preview["running_agent_count"] if selected_run_preview else 0,
            "waiting_agent_count": selected_run_preview["waiting_agent_count"] if selected_run_preview else 0,
            "done_agent_count": selected_run_preview["done_agent_count"] if selected_run_preview else 0,
            "failed_agent_count": selected_run_preview["failed_agent_count"] if selected_run_preview else 0,
            "idle_agent_count": selected_run_preview["idle_agent_count"] if selected_run_preview else 0,
        },
        "all_runs_summary": all_runs_summary,
        "selected_run_preview": selected_run_preview,
        "latest_run": latest_run,
        "recent_runs": [
            {
                "run_id": item["run_id"],
                "spec_id": item.get("spec_id", ""),
                "started_at": item.get("started_at"),
                "overall_status": _normalize_overall_status(item.get("overall_status", "unknown")),
                "artifact_score": item.get("final_result", {}).get("artifact_score"),
                "goal": item.get("goal", ""),
                "alerts": item.get("alerts", []),
                "roster_count": item.get("roster_count", len(item.get("roster", []))),
                "failed_agent_count": len(item.get("agent_state_board", {}).get("failed", [])),
                "running_agent_count": len(item.get("agent_state_board", {}).get("running", [])),
                "waiting_agent_count": len(item.get("agent_state_board", {}).get("waiting", [])),
                "done_agent_count": len(item.get("agent_state_board", {}).get("done", [])),
                "idle_agent_count": len(item.get("agent_state_board", {}).get("idle", [])),
            }
            for item in runs
        ],
    }


def get_ai_company_run_detail(connection: Connection, run_id: str) -> dict[str, Any]:
    sync_ai_company_runs(connection)
    row = connection.execute(
        "SELECT payload_json, alerts_json FROM ai_company_runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    if row is None:
        raise FileNotFoundError(run_id)
    payload = json.loads(row["payload_json"])
    payload["alerts"] = json.loads(row["alerts_json"])
    payload["overall_status"] = _normalize_overall_status(payload.get("overall_status"))
    payload["roster_count"] = payload.get("roster_count", len(payload.get("roster", [])))
    payload["selected_run_summary"] = _build_selected_run_summary(payload)
    recent_runs = _load_recent_run_payloads(connection)
    payload["recent_agent_failure_trend"] = _build_recent_agent_failure_trend(recent_runs)
    payload["agent_reliability_snapshot"] = _build_agent_reliability_snapshot(recent_runs)
    return payload
