from __future__ import annotations

import argparse
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_profile_resolver import build_profile_summary


ROOT = Path(__file__).resolve().parents[1]
DEFAULTS_PATH = ROOT / "configs" / "ai_company" / "task_harness.defaults.json"
DEFAULT_PHASE = "manual"
REPORT_NAME = "main_agent_memory_guard_report.json"
CHECKPOINT_JSON_NAME = "main_agent_memory_checkpoint.json"
CHECKPOINT_MD_NAME = "main_agent_memory_checkpoint.md"
CLAIM_LEDGER_NAME = "subagent_claim_ledger.json"


def read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except (json.JSONDecodeError, OSError):
        return {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_defaults() -> dict[str, Any]:
    return read_json(DEFAULTS_PATH)


def compact_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def excerpt_text(path: Path, limit: int) -> str:
    if not path.exists() or limit <= 0:
        return ""
    text = path.read_text(encoding="utf-8", errors="ignore")
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n[TRUNCATED]\n"


def estimate_tokens(char_count: int) -> int:
    return int(math.ceil(char_count / 4))


def count_words(text: str) -> int:
    return len(re.findall(r"\S+", text))


def rel_to_run(run_dir: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(run_dir.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def source_candidates(run_dir: Path) -> list[Path]:
    ai_dir = run_dir / "ai_company"
    paths = [
        run_dir / "summary.json",
        run_dir / "plan.json",
        ai_dir / "prep_report.json",
        ai_dir / "meeting_decision.json",
        ai_dir / "execution_summary.json",
        ai_dir / "reviewer_verdicts.json",
        ai_dir / "artifact_verify_report.json",
        ai_dir / "sens_evaluation_report.json",
        ai_dir / CLAIM_LEDGER_NAME,
    ]
    paths.extend(sorted((run_dir / "jobs").glob("*.json")))
    paths.extend(sorted((run_dir / "results").glob("*.status.json")))
    paths.extend(sorted((run_dir / "results").glob("*.prompt.txt")))
    paths.extend(sorted((run_dir / "results").glob("*.raw.txt")))
    paths.extend(sorted((run_dir / "results").glob("*.exec.log")))
    return [path for path in paths if path.exists()]


def collect_source_text(run_dir: Path, excerpt_chars: int) -> tuple[str, list[dict[str, Any]]]:
    blocks: list[str] = []
    sources: list[dict[str, Any]] = []
    for path in source_candidates(run_dir):
        raw = path.read_text(encoding="utf-8", errors="ignore")
        excerpt = raw if len(raw) <= excerpt_chars else raw[:excerpt_chars] + "\n[TRUNCATED]\n"
        blocks.append(f"--- {rel_to_run(run_dir, path)} ---\n{excerpt}")
        sources.append(
            {
                "path": rel_to_run(run_dir, path),
                "char_count": len(raw),
                "estimated_tokens": estimate_tokens(len(raw)),
                "included_excerpt_chars": len(excerpt),
            }
        )
    return "\n\n".join(blocks), sources


def latest_checkpoint(run_dir: Path, checkpoint_dir_name: str) -> dict[str, Any] | None:
    path = run_dir / checkpoint_dir_name / CHECKPOINT_JSON_NAME
    if not path.exists():
        return None
    payload = read_json(path)
    return payload or None


def summarize_tasks(run_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    jobs = [read_json(path) for path in sorted((run_dir / "jobs").glob("*.json"))]
    statuses = [read_json(path) for path in sorted((run_dir / "results").glob("*.status.json"))]
    status_by_id = {item.get("id"): item for item in statuses if item.get("id")}
    completed: list[dict[str, Any]] = []
    active: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    failure_statuses = {
        "FAILED",
        "OVERFLOW_DETECTED",
        "ROUTER_ERROR",
        "CHILD_TIMEOUT",
        "CHILD_LIMIT_REACHED",
        "NEEDS_REPLAN",
    }
    for job in jobs:
        task_id = job.get("id", "")
        status = status_by_id.get(task_id)
        row = {
            "task_id": task_id,
            "title": job.get("title", ""),
            "owner_role": job.get("owner_role", ""),
            "files": job.get("files", []),
            "success_check": job.get("success_check", ""),
        }
        if not status:
            active.append({**row, "state": "pending"})
            continue
        raw_status = str(status.get("status", "UNKNOWN"))
        if raw_status == "SUCCESS":
            completed.append({**row, "state": raw_status, "verification_note": status.get("verification_note", "")})
        elif raw_status in failure_statuses:
            failures.append({**row, "state": raw_status, "verification_note": status.get("verification_note", "")})
        else:
            active.append({**row, "state": raw_status, "verification_note": status.get("verification_note", "")})
    return active, completed, failures


def build_checkpoint(run_dir: Path, phase: str, source_text: str, max_chars: int, checkpoint_dir_name: str) -> dict[str, Any]:
    summary = read_json(run_dir / "summary.json")
    plan = read_json(run_dir / "plan.json")
    meeting = read_json(run_dir / "ai_company" / "meeting_decision.json")
    execution = read_json(run_dir / "ai_company" / "execution_summary.json")
    reviewer = read_json(run_dir / "ai_company" / "reviewer_verdicts.json")
    artifact = read_json(run_dir / "ai_company" / "artifact_verify_report.json")
    claim_ledger = read_json(run_dir / "ai_company" / CLAIM_LEDGER_NAME)
    profile_summary = build_profile_summary(run_dir)
    active_tasks, completed_tasks, failures = summarize_tasks(run_dir)

    decisions = [
        item
        for item in [
            meeting.get("decision_summary"),
            meeting.get("convergence_reason"),
            plan.get("strategy"),
        ]
        if item
    ]
    open_risks = list(meeting.get("open_risks", []))
    if failures:
        open_risks.append("At least one task has a failure-family status and needs bounded follow-up.")
    if artifact and not (artifact.get("parsed") or {}).get("all_passed", True):
        open_risks.append("Artifact verification has not passed all checks.")

    next_action = "Continue with the next bounded phase using this checkpoint plus the current task only."
    if failures:
        next_action = "Replan only the failed task scope; do not resend full prior logs."
    elif reviewer.get("replan_required_count", 0):
        next_action = "Use reviewer verdicts to create a narrower reassignment."

    compact_source = compact_spaces(source_text)
    if len(compact_source) > max_chars:
        compact_source = compact_source[:max_chars].rstrip() + " [TRUNCATED]"

    checkpoint = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_dir.name,
        "current_phase": phase,
        "goal": summary.get("task", ""),
        "strategy": summary.get("strategy") or plan.get("strategy", ""),
        "decisions": decisions,
        "active_tasks": active_tasks,
        "completed_tasks": completed_tasks,
        "failures": failures,
        "open_risks": open_risks,
        "next_recommended_action": next_action,
        "files_artifacts_map": {
            "summary": rel_to_run(run_dir, run_dir / "summary.json"),
            "plan": rel_to_run(run_dir, run_dir / "plan.json"),
            "meeting": rel_to_run(run_dir, run_dir / "ai_company" / "meeting_decision.json"),
            "execution": rel_to_run(run_dir, run_dir / "ai_company" / "execution_summary.json"),
            "reviewer": rel_to_run(run_dir, run_dir / "ai_company" / "reviewer_verdicts.json"),
            "artifact_verify": rel_to_run(run_dir, run_dir / "ai_company" / "artifact_verify_report.json"),
            "claim_ledger": rel_to_run(run_dir, run_dir / "ai_company" / CLAIM_LEDGER_NAME),
        },
        "claim_ledger": {
            "metrics": claim_ledger.get("metrics", {}),
            "claims": claim_ledger.get("claims", [])[:20],
        },
        "agent_profile_state": profile_summary,
        "condensed_state": compact_source,
    }
    out_dir = run_dir / checkpoint_dir_name
    write_json(out_dir / CHECKPOINT_JSON_NAME, checkpoint)
    (out_dir / CHECKPOINT_MD_NAME).write_text(render_checkpoint_markdown(checkpoint), encoding="utf-8")
    return checkpoint


def render_task_rows(tasks: list[dict[str, Any]]) -> str:
    if not tasks:
        return "- none"
    rows = []
    for task in tasks:
        rows.append(
            f"- {task.get('task_id', 'unknown')} [{task.get('state', 'unknown')}] "
            f"{task.get('title', '')} files={task.get('files', [])}"
        )
    return "\n".join(rows)


def render_checkpoint_markdown(checkpoint: dict[str, Any]) -> str:
    decisions = "\n".join(f"- {item}" for item in checkpoint.get("decisions", [])) or "- none"
    risks = "\n".join(f"- {item}" for item in checkpoint.get("open_risks", [])) or "- none"
    claim_ledger = checkpoint.get("claim_ledger", {})
    claims = claim_ledger.get("claims", []) if isinstance(claim_ledger, dict) else []
    claim_rows = "\n".join(
        f"- {item.get('task_id', 'unknown')} `{item.get('confidence', 'unknown')}`: {item.get('claim', '')} "
        f"(evidence refs: {len(item.get('evidence_refs', []))})"
        for item in claims[:10]
    ) or "- none"
    return "\n".join(
        [
            "# Main Agent Memory Checkpoint",
            "",
            f"- Run: `{checkpoint.get('run_id', '')}`",
            f"- Phase: `{checkpoint.get('current_phase', '')}`",
            f"- Goal: {checkpoint.get('goal', '')}",
            "",
            "## Decisions",
            decisions,
            "",
            "## Active Tasks",
            render_task_rows(checkpoint.get("active_tasks", [])),
            "",
            "## Completed Tasks",
            render_task_rows(checkpoint.get("completed_tasks", [])),
            "",
            "## Failures",
            render_task_rows(checkpoint.get("failures", [])),
            "",
            "## Open Risks",
            risks,
            "",
            "## Claim Ledger",
            claim_rows,
            "",
            "## Next Recommended Action",
            checkpoint.get("next_recommended_action", ""),
            "",
            "## Condensed State",
            checkpoint.get("condensed_state", ""),
            "",
        ]
    )


def update_report(run_dir: Path, checkpoint_dir_name: str, entry: dict[str, Any]) -> dict[str, Any]:
    report_path = run_dir / checkpoint_dir_name / REPORT_NAME
    report = read_json(report_path) if report_path.exists() else {"entries": []}
    report.setdefault("entries", []).append(entry)
    entries = report["entries"]
    report["memory_guard_enabled"] = True
    report["checkpoint_threshold_tokens"] = int(entry.get("checkpoint_threshold_tokens", entry.get("token_threshold", 0)))
    report["hard_threshold_tokens"] = int(entry.get("hard_threshold_tokens", entry.get("hard_threshold", 0)))
    report["threshold_source"] = str(entry.get("threshold_source", DEFAULTS_PATH.relative_to(ROOT).as_posix()))
    report["memory_checkpoint_count"] = sum(1 for item in entries if item.get("checkpoint_created"))
    report["max_estimated_main_agent_tokens"] = max((int(item.get("estimated_tokens", 0)) for item in entries), default=0)
    checkpoint_entries = [item for item in entries if item.get("checkpoint_created")]
    report["checkpoint_compression_rate"] = (
        round(checkpoint_entries[-1]["checkpoint_char_count"] / checkpoint_entries[-1]["char_count"], 3)
        if checkpoint_entries and checkpoint_entries[-1].get("char_count")
        else 1.0
    )
    report["last_phase"] = entry.get("phase")
    report["last_checkpoint_created"] = bool(entry.get("checkpoint_created"))
    report["checkpoint_decision"] = str(entry.get("checkpoint_decision", "unknown"))
    report["checkpoint_policy_result"] = str(entry.get("checkpoint_policy_result", "unknown"))
    write_json(report_path, report)
    return report


def run_guard(
    run_dir: Path,
    phase: str,
    enabled: bool,
    token_threshold: int,
    hard_threshold: int,
    excerpt_chars: int,
    checkpoint_max_chars: int,
    checkpoint_dir_name: str,
) -> dict[str, Any]:
    checkpoint_dir = run_dir / checkpoint_dir_name
    checkpoint_dir.mkdir(parents=True, exist_ok=True)

    if not enabled:
        entry = {
            "phase": phase,
            "enabled": False,
            "checkpoint_created": False,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "checkpoint_threshold_tokens": token_threshold,
            "hard_threshold_tokens": hard_threshold,
            "threshold_source": DEFAULTS_PATH.relative_to(ROOT).as_posix(),
            "checkpoint_decision": "disabled",
            "checkpoint_policy_result": "disabled",
        }
        update_report(run_dir, checkpoint_dir_name, entry)
        return entry

    source_text, sources = collect_source_text(run_dir, excerpt_chars)
    char_count = len(source_text)
    word_count = count_words(source_text)
    estimated = estimate_tokens(char_count)
    checkpoint_created = estimated > token_threshold
    checkpoint_payload = None
    if checkpoint_created:
        checkpoint_payload = build_checkpoint(run_dir, phase, source_text, checkpoint_max_chars, checkpoint_dir_name)

    checkpoint_char_count = len(json.dumps(checkpoint_payload, ensure_ascii=False)) if checkpoint_payload else 0
    entry = {
        "phase": phase,
        "enabled": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "char_count": char_count,
        "word_count": word_count,
        "estimated_tokens": estimated,
        "token_threshold": token_threshold,
        "hard_threshold": hard_threshold,
        "checkpoint_threshold_tokens": token_threshold,
        "hard_threshold_tokens": hard_threshold,
        "threshold_source": DEFAULTS_PATH.relative_to(ROOT).as_posix(),
        "hard_limit_exceeded": estimated > hard_threshold,
        "checkpoint_created": checkpoint_created,
        "checkpoint_decision": "created" if checkpoint_created else "skipped_below_threshold",
        "checkpoint_policy_result": "checkpoint_created" if checkpoint_created else "not_needed_below_threshold",
        "checkpoint_json": rel_to_run(run_dir, checkpoint_dir / CHECKPOINT_JSON_NAME) if checkpoint_created else "",
        "checkpoint_markdown": rel_to_run(run_dir, checkpoint_dir / CHECKPOINT_MD_NAME) if checkpoint_created else "",
        "checkpoint_char_count": checkpoint_char_count,
        "compression_rate": round(checkpoint_char_count / char_count, 3) if char_count and checkpoint_created else 1.0,
        "sources": sources,
    }
    update_report(run_dir, checkpoint_dir_name, entry)
    return entry


def main() -> None:
    defaults = load_defaults()
    parser = argparse.ArgumentParser(description="Create a run-local main-agent memory checkpoint when context risk is high.")
    parser.add_argument("run_dir")
    parser.add_argument("--phase", default=DEFAULT_PHASE)
    parser.add_argument("--enabled", default="1")
    parser.add_argument("--token-threshold", type=int, default=int(defaults.get("main_agent_memory_token_threshold", 24000)))
    parser.add_argument("--hard-threshold", type=int, default=int(defaults.get("main_agent_memory_hard_threshold", 32000)))
    parser.add_argument("--excerpt-chars", type=int, default=int(defaults.get("main_agent_memory_excerpt_chars", 1200)))
    parser.add_argument("--checkpoint-max-chars", type=int, default=int(defaults.get("main_agent_memory_checkpoint_max_chars", 8000)))
    parser.add_argument("--checkpoint-dir", default=str(defaults.get("main_agent_memory_checkpoint_dir", "ai_company")))
    args = parser.parse_args()

    entry = run_guard(
        Path(args.run_dir).resolve(),
        args.phase,
        args.enabled not in {"0", "false", "False"},
        args.token_threshold,
        args.hard_threshold,
        args.excerpt_chars,
        args.checkpoint_max_chars,
        args.checkpoint_dir,
    )
    print(json.dumps(entry, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
