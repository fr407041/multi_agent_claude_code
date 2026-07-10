from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from agent_profile_resolver import apply_profile_to_job, load_agent_profiles, resolve_run_profile_mode


ROLE_ORDER = [
    "meeting_coordinator",
    "planner_agent",
    "risk_reviewer",
    "decision_agent",
]

CHECKPOINT_PATH = Path("ai_company") / "main_agent_memory_checkpoint.json"
LIVE_TRANSCRIPT_PATH = Path("ai_company") / "live_meeting_transcript.jsonl"
LIVE_MEETING_ROLES = ["meeting_coordinator", "planner_agent", "risk_reviewer", "decision_agent"]


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_checkpoint(run_dir: Path) -> dict:
    path = run_dir / CHECKPOINT_PATH
    if not path.exists():
        return {}
    try:
        return read_json(path)
    except json.JSONDecodeError:
        return {}


def infer_owner_role(files: list[str], instruction: str = "", title: str = "") -> str:
    lowered = [item.lower() for item in files]
    instruction_lower = instruction.lower()
    title_lower = title.lower()
    combined = f"{instruction_lower} {title_lower}"
    if any(keyword in combined for keyword in ["synthesize", "analysis", "verdict", "summary", "decision"]):
        return "synthesis_agent"
    if any(keyword in combined for keyword in ["research", "evidence", "verify", "fetch", "latest source", "anchor"]):
        return "research_agent"
    if any(item.endswith((".tsx", ".jsx", ".css", ".scss", ".html")) for item in lowered):
        return "executor_frontend"
    if any(item.endswith((".md", ".txt", ".rst")) for item in lowered):
        return "executor_docs"
    return "executor_backend"


def stable_digest(payload: object) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def parse_json_object(text: str) -> dict:
    stripped = text.lstrip("\ufeff").strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    parsed = json.loads(stripped)
    if not isinstance(parsed, dict):
        raise ValueError("live meeting response must be a JSON object")
    return parsed


def compact_state(
    goal: str,
    assignments: list[dict],
    summary: dict,
    round_no: int,
    round_limit: int,
    previous_turns: list[dict],
) -> dict:
    return {
        "goal": goal[:1200],
        "round": round_no,
        "round_limit": round_limit,
        "assignments": [
            {
                "task_id": item.get("task_id"),
                "owner_role": item.get("owner_role"),
                "scope": list(item.get("scope", []))[:3],
                "acceptance_criteria": list(item.get("acceptance_criteria", []))[:2],
            }
            for item in assignments
        ],
        "memory_checkpoint": summary.get("memory_checkpoint", {}),
        "history": [
            {
                "role": item.get("role", ""),
                "summary": str(item.get("summary", ""))[:360],
                "risk_flags": list(item.get("risk_flags", []))[:5],
                "decision_state": item.get("decision_state", ""),
            }
            for item in previous_turns[-6:]
        ],
    }


def live_meeting_prompt(role: str, state: dict) -> str:
    return (
        "Return only strict JSON. No markdown. No explanation.\n"
        "You are one role in a bounded multi-agent planning meeting.\n"
        "Do not request full repository context. Do not expand task scope.\n"
        "Return this JSON shape exactly: "
        '{"summary":"short role-specific conclusion","proposed_actions":["..."],'
        '"risk_flags":["..."],"decision_state":"proposed|reviewed|MEETING_READY|MEETING_CONTINUE|MEETING_NEEDS_REPLAN"}.\n'
        f"Role: {role}\n"
        f"Compact state:\n{json.dumps(state, ensure_ascii=False, sort_keys=True)}"
    )


def extract_message_text(body: str) -> str:
    parsed = json.loads(body)
    if isinstance(parsed.get("content"), list):
        return "\n".join(str(item.get("text", "")) for item in parsed["content"] if isinstance(item, dict))
    if isinstance(parsed.get("choices"), list) and parsed["choices"]:
        choice = parsed["choices"][0]
        message = choice.get("message", {}) if isinstance(choice, dict) else {}
        return str(message.get("content") or choice.get("text") or "")
    if isinstance(parsed.get("message"), dict):
        return str(parsed["message"].get("content", ""))
    return body


def call_ccr_for_live_turn(role: str, state: dict) -> str:
    url = os.environ.get("CCR_MESSAGES_URL", "http://127.0.0.1:3456/v1/messages")
    model = os.environ.get("CLAUDE_MODEL_ALIAS") or os.environ.get("CCR_PREFERRED_MODEL")
    if not model:
        raise RuntimeError("CLAUDE_MODEL_ALIAS or CCR_PREFERRED_MODEL is required for live meeting mode")
    api_key = os.environ.get("CCR_API_KEY", os.environ.get("ANTHROPIC_API_KEY", "local-router-token"))
    max_tokens = int(os.environ.get("CCR_MAX_OUTPUT_TOKENS", "1024"))
    timeout_sec = int(os.environ.get("AI_COMPANY_LIVE_MEETING_TIMEOUT_SEC", os.environ.get("AI_COMPANY_CALL_TIMEOUT_SEC", "90")))
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": live_meeting_prompt(role, state)}],
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-api-key": api_key, "anthropic-version": "2023-06-01"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_sec) as response:
        return extract_message_text(response.read().decode("utf-8", errors="ignore"))


def write_transcript_event(run_dir: Path, event: dict) -> None:
    path = run_dir / LIVE_TRANSCRIPT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def coerce_live_turn(role: str, round_no: int, payload: dict) -> dict:
    summary = str(payload.get("summary") or "")[:600] or f"{role} returned an empty summary."
    proposed_actions = payload.get("proposed_actions")
    if not isinstance(proposed_actions, list):
        proposed_actions = []
    risk_flags = payload.get("risk_flags")
    if not isinstance(risk_flags, list):
        risk_flags = []
    decision_state = str(payload.get("decision_state") or ("reviewed" if role == "risk_reviewer" else "proposed"))
    return {
        "round": round_no,
        "role": role,
        "summary": summary,
        "proposed_actions": [str(item)[:240] for item in proposed_actions[:5]],
        "risk_flags": [str(item)[:80] for item in risk_flags[:8]],
        "decision_state": decision_state,
    }


def live_turn(role: str, run_dir: Path, state: dict, round_no: int) -> dict:
    prompt_hash = stable_digest({"role": role, "state": state})
    started = {
        "round": round_no,
        "role": role,
        "prompt_hash": prompt_hash,
        "parse_status": "started",
    }
    try:
        raw_text = call_ccr_for_live_turn(role, state)
        parsed = parse_json_object(raw_text)
        turn = coerce_live_turn(role, round_no, parsed)
        write_transcript_event(
            run_dir,
            {
                **started,
                "parse_status": "ok",
                "response_excerpt": raw_text[:800],
                "decision_state": turn.get("decision_state", ""),
            },
        )
        return turn
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError, RuntimeError) as exc:
        write_transcript_event(
            run_dir,
            {
                **started,
                "parse_status": "failed",
                "error": str(exc)[:500],
            },
        )
        raise


def normalize_scope(files: list[str]) -> list[str]:
    deduped: list[str] = []
    for item in files:
        if item not in deduped:
            deduped.append(item)
    return deduped[:3]


def base_assignments(run_dir: Path) -> list[dict]:
    jobs_dir = run_dir / "jobs"
    assignments = []
    for job_path in sorted(jobs_dir.glob("job-*.json")):
        job = read_json(job_path)
        task_id = job.get("id", job_path.stem)
        instruction = str(job.get("instruction", ""))
        title = str(job.get("title", task_id))
        scope = normalize_scope(job.get("files", []))
        owner_role = str(job.get("owner_role") or infer_owner_role(scope, instruction, title))
        assignments.append(
            {
                "task_id": task_id,
                "owner_role": owner_role,
                "agent_profile": job.get("agent_profile") or owner_role,
                "profile_mode": job.get("profile_mode", "auto"),
                "scope": scope,
                "depends_on": [],
                "acceptance_criteria": [
                    job.get("success_check", "Return a concise verified result."),
                    "status.json must exist",
                ],
                "fallback_plan": "If the worker fails, overflows, or asks for broader scope, trigger narrower replan.",
                "_source_instruction": instruction,
                "_source_title": title,
                "_source_file_count": len(job.get("files", [])),
            }
        )
    return assignments


def append_reviewer_tasks(assignments: list[dict]) -> list[dict]:
    out: list[dict] = []
    for item in assignments:
        clean = {k: v for k, v in item.items() if not k.startswith("_")}
        out.append(clean)
        out.append(
            {
                "task_id": f"{item['task_id']}-review",
                "owner_role": "reviewer_worker",
                "agent_profile": "reviewer_worker",
                "profile_mode": item.get("profile_mode", "auto"),
                "scope": list(clean["scope"]),
                "depends_on": [item["task_id"]],
                "acceptance_criteria": [
                    "raw/status/test artifacts are present",
                    "before/after evidence is reviewable",
                ],
                "fallback_plan": "If evidence is missing or false success is suspected, return REPAIR_REQUIRED or REPLAN_REQUIRED.",
            }
        )
    return out


def planner_turn(assignments: list[dict], summary: dict, round_no: int) -> dict:
    broad_tasks = [item["task_id"] for item in assignments if item.get("_source_file_count", len(item["scope"])) > 3]
    note = "Prepare a bounded task graph from the current plan."
    if broad_tasks:
        note = f"Narrow broad tasks before dispatch: {', '.join(broad_tasks)}."
    checkpoint = summary.get("memory_checkpoint") or {}
    proposed_actions = [
        f"Keep total execution jobs at {len(assignments)} before reviewer expansion.",
        "Ensure every task stays at 3 files or fewer.",
        f"Use strategy: {summary.get('strategy', 'bounded planning before dispatch.')}",
    ]
    if checkpoint:
        proposed_actions.append(
            "Use main-agent memory checkpoint as condensed prior state; do not replay full prior logs."
        )
    return {
        "round": round_no,
        "role": "planner_agent",
        "summary": note,
        "proposed_actions": proposed_actions,
        "risk_flags": ["scope_too_broad"] if broad_tasks else [],
        "decision_state": "proposed",
    }


def risk_turn(assignments: list[dict], summary: dict, round_no: int, proposal_repeat: bool, round_limit: int) -> dict:
    risk_flags: list[str] = []
    proposed_actions: list[str] = []
    if any(len(item["scope"]) > 3 for item in assignments):
        risk_flags.append("scope_too_broad")
        proposed_actions.append("Reduce each scope to 3 files or fewer.")
    if any(not item["scope"] for item in assignments):
        risk_flags.append("empty_scope")
        proposed_actions.append("Replan tasks with empty scope before dispatch.")
    if as_int(summary.get("metrics", {}).get("workers_overflowed")) > 0:
        risk_flags.append("historical_overflow_pressure")
        proposed_actions.append("Prefer smaller evidence-first tasks because historical overflow already occurred.")
    if as_int(summary.get("metrics", {}).get("same_failure_again")) > 0:
        risk_flags.append("repeat_failure_signal")
        proposed_actions.append("Block infinite retry and require narrower reassignment.")
    if proposal_repeat:
        risk_flags.append("proposal_repeat_detected")
        proposed_actions.append(f"Stop after round {round_limit} if the same unresolved proposal repeats.")
    return {
        "round": round_no,
        "role": "risk_reviewer",
        "summary": "Review scope, loop risk, and historical failure pressure before final dispatch.",
        "proposed_actions": proposed_actions or ["No critical risks found after bounded review."],
        "risk_flags": risk_flags,
        "decision_state": "reviewed",
    }


def coordinator_turn(goal: str, round_no: int, round_limit: int) -> dict:
    return {
        "round": round_no,
        "role": "meeting_coordinator",
        "summary": f"Round {round_no}/{round_limit}: keep discussion bounded and converge to dispatchable tasks.",
        "proposed_actions": [
            "Do not discuss the whole repository.",
            "Require explicit owner, scope, acceptance, and fallback for every task.",
            "Close the meeting once scope and risks are bounded.",
        ],
        "risk_flags": [],
        "decision_state": f"goal:{goal}",
    }


def decision_turn(
    assignments: list[dict],
    risk_flags: list[str],
    round_no: int,
    round_limit: int,
    proposal_repeat: bool,
) -> tuple[dict, list[dict], str, str]:
    final_assignments = []
    for item in assignments:
        narrowed = dict(item)
        narrowed["scope"] = normalize_scope(item["scope"])
        final_assignments.append(narrowed)

    unresolved = [flag for flag in risk_flags if flag in {"scope_too_broad", "empty_scope"}]
    loop_guard_triggered = proposal_repeat and round_no >= round_limit
    if unresolved and round_no >= round_limit:
        status = "MEETING_NEEDS_REPLAN"
        reason = "Round limit reached with unresolved scope issues."
    elif unresolved:
        status = "MEETING_CONTINUE"
        reason = "Unresolved scope issues remain; continue to the next bounded round."
    elif loop_guard_triggered:
        status = "MEETING_NEEDS_REPLAN"
        reason = "Repeated proposal detected at the round limit."
    else:
        status = "MEETING_READY"
        reason = "Assignments are bounded and ready for reviewer expansion."

    turn = {
        "round": round_no,
        "role": "decision_agent",
        "summary": reason,
        "proposed_actions": [
            "Freeze the current bounded assignment set.",
            "Append reviewer tasks after execution owners are finalized.",
            "Stop the meeting once a dispatchable plan exists.",
        ],
        "risk_flags": risk_flags,
        "decision_state": status,
    }
    return turn, final_assignments, status, reason


def as_int(value: object) -> int:
    if value in (None, "", False):
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(str(value))
    except ValueError:
        return 0


def build_minutes(goal: str, final_status: str, rounds_used: int, round_limit: int, decision_reason: str, final_assignments: list[dict]) -> str:
    owner_summary: dict[str, int] = {}
    for item in final_assignments:
        owner_summary[item["owner_role"]] = owner_summary.get(item["owner_role"], 0) + 1
    owner_text = ", ".join(f"{role}={count}" for role, count in sorted(owner_summary.items())) or "none"
    return (
        f"Goal: {goal}\n"
        f"Status: {final_status}\n"
        f"Rounds used: {rounds_used}/{round_limit}\n"
        f"Decision: {decision_reason}\n"
        f"Assignments: {len(final_assignments)} tasks before reviewer expansion ({owner_text})."
    )


def build_meeting_decision(run_dir: Path, fallback_task: str = "") -> dict:
    summary_path = run_dir / "summary.json"
    summary = read_json(summary_path) if summary_path.exists() else {}
    checkpoint = read_checkpoint(run_dir)
    if checkpoint:
        summary["memory_checkpoint"] = {
            "current_phase": checkpoint.get("current_phase", ""),
            "decisions": checkpoint.get("decisions", []),
            "failures": checkpoint.get("failures", []),
            "open_risks": checkpoint.get("open_risks", []),
            "next_recommended_action": checkpoint.get("next_recommended_action", ""),
        }
    plan = read_json(run_dir / "plan.json")
    goal = summary.get("task", fallback_task)
    round_limit = int(os.environ.get("AI_COMPANY_MEETING_ROUND_LIMIT", "3"))
    spec = {
        "agent_profile_mode": summary.get("agent_profile_mode") or plan.get("agent_profile_mode", "auto"),
        "jobs": plan.get("jobs", []),
        "expectations": summary.get("expectations", {}),
    }
    run_profile_mode, profile_reasons = resolve_run_profile_mode(spec, None)
    profile_registry = load_agent_profiles()
    raw_assignments = base_assignments(run_dir)

    discussion_log = []
    proposal_digests: list[str] = []
    final_status = "MEETING_READY"
    decision_reason = "Assignments are bounded and ready for reviewer expansion."
    rounds_used = 0
    current_assignments = raw_assignments
    meeting_mode = os.environ.get("AI_COMPANY_MEETING_MODE", "deterministic").strip().lower() or "deterministic"
    if meeting_mode not in {"deterministic", "live"}:
        meeting_mode = "deterministic"
    live_meeting_used = False
    live_degraded = False
    degrade_reason = ""
    live_turn_count = 0

    if meeting_mode == "live":
        transcript_path = run_dir / LIVE_TRANSCRIPT_PATH
        if transcript_path.exists():
            transcript_path.unlink()
        try:
            for round_no in range(1, round_limit + 1):
                rounds_used = round_no
                round_turns: list[dict] = []
                for role in LIVE_MEETING_ROLES:
                    state = compact_state(goal, current_assignments, summary, round_no, round_limit, discussion_log + round_turns)
                    turn = live_turn(role, run_dir, state, round_no)
                    round_turns.append(turn)
                    live_turn_count += 1
                discussion_log.extend(round_turns)

                digest = stable_digest([{k: v for k, v in item.items() if not k.startswith("_")} for item in current_assignments])
                proposal_repeat = digest in proposal_digests
                proposal_digests.append(digest)
                risk_flags = sorted({flag for item in round_turns for flag in item.get("risk_flags", [])})
                decision_entry, current_assignments, final_status, decision_reason = decision_turn(
                    current_assignments,
                    risk_flags,
                    round_no,
                    round_limit,
                    proposal_repeat,
                )
                decision_entry["summary"] = f"Validated live meeting output. {decision_entry['summary']}"
                discussion_log.append(decision_entry)
                if final_status == "MEETING_READY":
                    break
            live_meeting_used = True
        except Exception as exc:
            live_degraded = True
            degrade_reason = str(exc)[:500]
            discussion_log = []
            proposal_digests = []
            final_status = "MEETING_READY"
            decision_reason = "Assignments are bounded and ready for reviewer expansion."
            rounds_used = 0
            current_assignments = raw_assignments

    if meeting_mode != "live" or live_degraded:
        for round_no in range(1, round_limit + 1):
            rounds_used = round_no
            discussion_log.append(coordinator_turn(goal, round_no, round_limit))
            planner_entry = planner_turn(current_assignments, summary, round_no)
            discussion_log.append(planner_entry)

            digest = stable_digest([{k: v for k, v in item.items() if not k.startswith("_")} for item in current_assignments])
            proposal_repeat = digest in proposal_digests
            proposal_digests.append(digest)

            risk_entry = risk_turn(current_assignments, summary, round_no, proposal_repeat, round_limit)
            discussion_log.append(risk_entry)
            decision_entry, current_assignments, final_status, decision_reason = decision_turn(
                current_assignments,
                risk_entry["risk_flags"],
                round_no,
                round_limit,
                proposal_repeat,
            )
            discussion_log.append(decision_entry)
            if final_status == "MEETING_READY":
                break

    final_assignments = append_reviewer_tasks(current_assignments)
    final_assignments = [
        apply_profile_to_job(item, run_profile_mode, profile_registry)
        for item in final_assignments
    ]
    minutes = build_minutes(goal, final_status, rounds_used, round_limit, decision_reason, current_assignments)

    return {
        "meeting_id": f"{summary.get('run_id', run_dir.name)}-meeting",
        "meeting_status": final_status,
        "meeting_mode": meeting_mode,
        "live_meeting_used": live_meeting_used,
        "live_turn_count": live_turn_count,
        "live_degraded": live_degraded,
        "degrade_reason": degrade_reason,
        "transcript_file": str((run_dir / LIVE_TRANSCRIPT_PATH).relative_to(run_dir).as_posix()) if (run_dir / LIVE_TRANSCRIPT_PATH).exists() else "",
        "goal": goal,
        "constraints": [
            "single task scope should not exceed 3 files",
            "do not send full repo context to a child worker",
            "meeting output must remain structured and concise",
            "if a main-agent memory checkpoint exists, use it instead of replaying full prior logs",
        ],
        "decision_summary": plan.get("strategy", "Use bounded planning before dispatch."),
        "run_profile_mode": run_profile_mode,
        "profile_resolution_reasons": profile_reasons,
        "meeting_minutes": minutes,
        "rounds_used": rounds_used,
        "round_limit": round_limit,
        "convergence_reason": decision_reason,
        "discussion_log": discussion_log,
        "task_assignments": final_assignments,
        "open_risks": [
            "token overflow on broad scope",
            "router transport instability",
            "false success without verified file change",
            *checkpoint.get("open_risks", []),
        ],
        "stop_conditions": [
            "meeting exceeds bounded rounds",
            "task has no owner",
            "task scope exceeds allowed file limit",
            "repeat proposal is detected at the round limit",
        ],
    }


def main() -> None:
    if len(sys.argv) not in {2, 3, 4}:
        raise SystemExit("Usage: run_ai_company_meeting.py <run_dir> [out_file] [task]")
    run_dir = Path(sys.argv[1]).resolve()
    out_file = Path(sys.argv[2]).resolve() if len(sys.argv) >= 3 else None
    task = sys.argv[3] if len(sys.argv) >= 4 else ""
    payload = build_meeting_decision(run_dir, task)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if out_file:
        out_file.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
