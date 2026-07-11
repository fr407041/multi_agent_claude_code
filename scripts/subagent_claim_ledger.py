from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LEDGER_NAME = "subagent_claim_ledger.json"


def read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except (json.JSONDecodeError, OSError):
        return {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def rel_to_run(run_dir: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(run_dir.resolve()).as_posix()
    except (OSError, ValueError):
        return path.as_posix()


def resolve_path(raw_path: str, run_dir: Path) -> Path:
    raw = str(raw_path or "").strip()
    if not raw:
        return Path()
    if raw.startswith("/workspace/"):
        return Path.cwd() / raw.removeprefix("/workspace/")
    path = Path(raw)
    if path.is_absolute():
        return path
    return run_dir / path


def read_text_if_exists(path: Path, limit: int = 6000) -> str:
    if not path or not path.exists() or not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="ignore")
    return text[:limit]


def compact_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def clean_claim_text(text: str) -> str:
    text = re.sub(r"^\s*(?:[-*]|\d+[.)]|STATUS:|SUMMARY:|FILES:)\s*", "", text, flags=re.IGNORECASE)
    text = compact_spaces(text)
    return text[:320].rstrip()


def candidate_claim_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = clean_claim_text(raw_line)
        if not line:
            continue
        if len(line) < 18:
            continue
        if line.lower().startswith(("mock test output", "status:")):
            continue
        lines.append(line)
    if not lines:
        sentences = re.split(r"(?<=[.!?])\s+", compact_spaces(text))
        lines = [clean_claim_text(item) for item in sentences if len(clean_claim_text(item)) >= 18]
    deduped: list[str] = []
    seen: set[str] = set()
    for line in lines:
        key = line.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(line)
    return deduped[:5]


def evidence_refs_for_status(run_dir: Path, status_path: Path, status: dict[str, Any]) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = [{"type": "status_file", "path": rel_to_run(run_dir, status_path)}]

    raw_path = resolve_path(str(status.get("raw_file", "")), run_dir)
    if raw_path.exists():
        refs.append({"type": "raw_file", "path": rel_to_run(run_dir, raw_path)})
        if raw_path.name.endswith(".raw.txt"):
            prompt_path = raw_path.with_name(raw_path.name.replace(".raw.txt", ".prompt.txt"))
            if prompt_path.exists():
                refs.append({"type": "prompt_file", "path": rel_to_run(run_dir, prompt_path)})

    test_path = resolve_path(str(status.get("test_output_file", "")), run_dir)
    if test_path.exists():
        refs.append({"type": "test_output", "path": rel_to_run(run_dir, test_path)})

    scope_path = resolve_path(str(status.get("scope_path", "")), run_dir)
    for changed in status.get("actual_changed_files", []) or []:
        changed_path = scope_path / str(changed)
        refs.append({"type": "changed_file", "path": rel_to_run(run_dir, changed_path)})
    return refs


def build_claims_for_status(run_dir: Path, status_path: Path, status: dict[str, Any]) -> list[dict[str, Any]]:
    task_id = str(status.get("id") or status_path.stem.replace(".status", ""))
    refs = evidence_refs_for_status(run_dir, status_path, status)
    raw_path = resolve_path(str(status.get("raw_file", "")), run_dir)
    raw_text = read_text_if_exists(raw_path)
    scope_path = resolve_path(str(status.get("scope_path", "")), run_dir)
    summary_text = read_text_if_exists(scope_path / "summary.md")

    explicit_claims = status.get("key_claims")
    explicit_claim_rows: list[dict[str, Any]] = []
    if isinstance(explicit_claims, list):
        for item in explicit_claims:
            if isinstance(item, dict):
                claim_text = clean_claim_text(str(item.get("claim", "")))
                explicit_claim_rows.append(
                    {
                        "claim": claim_text,
                        "evidence_refs": item.get("evidence_refs") if "evidence_refs" in item else None,
                        "evidence_refs_scoped": item.get("evidence_refs_scoped") if "evidence_refs_scoped" in item else None,
                        "confidence": item.get("confidence"),
                        "limitations": item.get("limitations"),
                        "handoff_next": item.get("handoff_next"),
                    }
                )
            else:
                explicit_claim_rows.append({"claim": clean_claim_text(str(item)), "evidence_refs": None})
        claim_texts = [item["claim"] for item in explicit_claim_rows]
        claim_texts = [item for item in claim_texts if item]
    else:
        claim_texts = candidate_claim_lines("\n".join([summary_text, raw_text, str(status.get("verification_note", "")), str(status.get("success_check", ""))]))

    if not claim_texts and status.get("verification_note"):
        claim_texts = [clean_claim_text(str(status["verification_note"]))]

    confidence = str(status.get("confidence", "medium")).lower()
    if status.get("status") != "SUCCESS":
        confidence = "low"
    if confidence not in {"high", "medium", "low"}:
        confidence = "medium"

    limitations = status.get("limitations") if isinstance(status.get("limitations"), list) else []
    if not limitations and status.get("status") != "SUCCESS":
        limitations = [str(status.get("verification_note", "Subagent did not complete successfully."))]

    claims: list[dict[str, Any]] = []
    for index, claim_text in enumerate(claim_texts[:5], start=1):
        explicit_row = explicit_claim_rows[index - 1] if index - 1 < len(explicit_claim_rows) else {}
        claim_refs = explicit_row.get("evidence_refs")
        if claim_refs is None:
            claim_refs = refs
        claim_refs_scoped = explicit_row.get("evidence_refs_scoped")
        if claim_refs_scoped is None:
            claim_refs_scoped = [
                {
                    "path": item.get("path", ""),
                    "scope": "generated_current_run",
                    "note": f"Derived from {item.get('type', 'artifact')} for this run.",
                }
                for item in refs
            ]
        claim_confidence = str(explicit_row.get("confidence") or confidence).lower()
        if claim_confidence not in {"high", "medium", "low"}:
            claim_confidence = confidence
        claim_limitations = explicit_row.get("limitations")
        if not isinstance(claim_limitations, list):
            claim_limitations = limitations
        claims.append(
            {
                "claim_id": f"{task_id}-claim-{index:03d}",
                "task_id": task_id,
                "owner_role": status.get("owner_role", ""),
                "agent_profile": status.get("agent_profile", ""),
                "profile_mode": status.get("profile_mode", ""),
                "effective_policy_level": status.get("effective_policy_level", ""),
                "effective_tool_policy": status.get("effective_tool_policy", ""),
                "claim": claim_text,
                "summary": status.get("subagent_summary") or claim_text,
                "evidence_refs": claim_refs,
                "evidence_refs_scoped": claim_refs_scoped,
                "confidence": claim_confidence,
                "limitations": claim_limitations,
                "handoff_next": explicit_row.get("handoff_next") or status.get("handoff_next") or "Use this claim as bounded input only if evidence refs remain available.",
            }
        )
    return claims


def build_claim_ledger(run_dir: Path) -> dict[str, Any]:
    results_dir = run_dir / "results"
    claims: list[dict[str, Any]] = []
    task_ids: set[str] = set()
    checkout_verifications: list[dict[str, Any]] = []
    for status_path in sorted(results_dir.glob("*.status.json")):
        status = read_json(status_path)
        task_id = str(status.get("id") or status_path.stem.replace(".status", ""))
        task_ids.add(task_id)
        checkout = status.get("current_checkout_verification")
        if isinstance(checkout, dict):
            checkout_verifications.append(checkout)
        claims.extend(build_claims_for_status(run_dir, status_path, status))

    claims_with_evidence = sum(1 for item in claims if item.get("evidence_refs"))
    high_confidence_without_limits = [
        item
        for item in claims
        if item.get("confidence") == "high" and not item.get("evidence_refs")
    ]
    metrics = {
        "task_count": len(task_ids),
        "claim_count": len(claims),
        "claims_with_evidence": claims_with_evidence,
        "claim_coverage_rate": round(claims_with_evidence / len(claims), 3) if claims else 0.0,
        "uncertainty_gap_count": len(high_confidence_without_limits),
        "tasks_with_claims": len({item.get("task_id") for item in claims}),
    }
    return {
        "schema_version": 1,
        "run_id": run_dir.name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "claims": claims,
        "metrics": metrics,
        "current_checkout_verification": {
            "mock_harness_executed": True,
            "dashboard_runtime_bundled": any(bool(item.get("dashboard_runtime_bundled") or item.get("dashboard_source_present")) for item in checkout_verifications),
            "dashboard_runtime_verified_by_mock_harness": False,
            "dashboard_runtime_verified": any(bool(item.get("dashboard_runtime_verified")) for item in checkout_verifications),
            "dashboard_runtime_reason": "Dashboard source is bundled; the mock harness does not start web services. Run the dashboard smoke command to verify runtime.",
            "dashboard_smoke_command": "bash agent_os_mvp/smoke-dashboard.sh",
            "dashboard_tracking_issue": None,
        },
    }


def write_claim_ledger(run_dir: Path) -> dict[str, Any]:
    ledger = build_claim_ledger(run_dir)
    write_json(run_dir / "ai_company" / LEDGER_NAME, ledger)
    return ledger


def validate_claim_contract(ledger: dict[str, Any], accepted_task_ids: set[str] | None = None) -> dict[str, Any]:
    claims = ledger.get("claims", []) if isinstance(ledger.get("claims"), list) else []
    accepted_task_ids = accepted_task_ids or set()
    claims_by_task: dict[str, list[dict[str, Any]]] = {}
    missing_evidence: list[str] = []
    for claim in claims:
        task_id = str(claim.get("task_id", ""))
        claims_by_task.setdefault(task_id, []).append(claim)
        if not claim.get("evidence_refs"):
            missing_evidence.append(str(claim.get("claim_id", "")))

    accepted_without_claim = sorted(task_id for task_id in accepted_task_ids if not claims_by_task.get(task_id))
    return {
        "all_claims_have_evidence": not missing_evidence,
        "accepted_tasks_have_claim": not accepted_without_claim,
        "missing_evidence_claim_ids": missing_evidence,
        "accepted_without_claim_task_ids": accepted_without_claim,
        "claim_coverage_rate": ledger.get("metrics", {}).get("claim_coverage_rate", 0.0),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a run-local subagent claim ledger.")
    parser.add_argument("run_dir")
    args = parser.parse_args()
    ledger = write_claim_ledger(Path(args.run_dir).resolve())
    print(json.dumps(ledger, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
