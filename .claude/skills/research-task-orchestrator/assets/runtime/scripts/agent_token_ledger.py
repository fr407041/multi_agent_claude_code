from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any


LEDGER_PATH = Path("ai_company") / "agent_token_ledger.json"


def estimate_tokens_from_chars(char_count: int) -> int:
    return int(math.ceil(max(char_count, 0) / 4))


def estimate_tokens(text: str) -> int:
    return estimate_tokens_from_chars(len(text or ""))


def token_usage_from_text(
    input_text: str = "",
    output_text: str = "",
    provider_usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return token_usage_from_counts(len(input_text or ""), len(output_text or ""), provider_usage)


def token_usage_from_counts(
    input_chars: int = 0,
    output_chars: int = 0,
    provider_usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    provider_usage = provider_usage or {}
    input_chars = max(0, int(input_chars))
    output_chars = max(0, int(output_chars))
    provider_input = provider_usage.get("input_tokens") or provider_usage.get("prompt_tokens")
    provider_output = provider_usage.get("output_tokens") or provider_usage.get("completion_tokens")
    provider_total = provider_usage.get("total_tokens")
    if provider_total is None and provider_input is not None and provider_output is not None:
        provider_total = int(provider_input) + int(provider_output)
    return {
        "input_chars": input_chars,
        "output_chars": output_chars,
        "estimated_input_tokens": estimate_tokens_from_chars(input_chars),
        "estimated_output_tokens": estimate_tokens_from_chars(output_chars),
        "estimated_total_tokens": estimate_tokens_from_chars(input_chars + output_chars),
        "provider_input_tokens": provider_input,
        "provider_output_tokens": provider_output,
        "provider_total_tokens": provider_total,
        "estimation_method": "ceil(chars/4)",
    }


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_text(path: Path) -> str:
    if not path or not path.exists() or not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="ignore")


def count_text_chars(path: Path, chunk_bytes: int = 65536) -> int:
    if not path or not path.exists() or not path.is_file():
        return 0
    total = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_bytes), b""):
            total += len(chunk.decode("utf-8", errors="ignore"))
    return total


def workspace_path_to_local(value: str, run_dir: Path) -> Path:
    if not value:
        return Path()
    path = Path(value)
    if path.exists():
        return path
    if value.startswith("/workspace/"):
        candidate = run_dir
        parts = value.replace("\\", "/").split("/")
        if "results" in parts:
            idx = parts.index("results")
            candidate = run_dir / Path(*parts[idx:])
            if candidate.exists():
                return candidate
    return path


def turn_record(
    *,
    run_dir: Path,
    phase: str,
    agent: str,
    task_id: str,
    status: str = "",
    source_file: str = "",
    token_usage: dict[str, Any] | None = None,
    input_text: str = "",
    output_text: str = "",
    transport: str = "",
    attempt: int = 1,
    duration_sec: float = 0.0,
    context_guard: dict[str, Any] | None = None,
    warning_threshold: int,
) -> dict[str, Any]:
    usage = token_usage or token_usage_from_text(input_text, output_text)
    total = int(usage.get("provider_total_tokens") or usage.get("estimated_total_tokens") or 0)
    return {
        "phase": phase,
        "agent": agent or "unknown_agent",
        "task_id": task_id,
        "status": status,
        "transport": transport,
        "attempt": attempt,
        "duration_sec": duration_sec,
        "source_file": source_file,
        "token_usage": usage,
        "estimated_total_tokens": int(usage.get("estimated_total_tokens", 0)),
        "effective_total_tokens": total,
        "overflow_risk": total >= warning_threshold,
        "context_guard": context_guard or {},
    }


def collect_status_turns(run_dir: Path, warning_threshold: int) -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    results_dir = run_dir / "results"
    for status_path in sorted(results_dir.glob("*.status.json")):
        try:
            status = read_json(status_path)
        except json.JSONDecodeError:
            continue
        raw_path = workspace_path_to_local(str(status.get("raw_file", "")), run_dir)
        exec_log_path = workspace_path_to_local(str(status.get("exec_log_file", "")), run_dir)
        raw_chars = count_text_chars(raw_path)
        exec_log_chars = 0 if exec_log_path == raw_path else count_text_chars(exec_log_path)
        instruction = str(status.get("prompt_excerpt") or status.get("instruction") or status.get("success_check") or "")
        verification_chars = len(str(status.get("verification_note", "")))
        output_chars = raw_chars + exec_log_chars + verification_chars
        usage = status.get("token_usage") if isinstance(status.get("token_usage"), dict) else None
        if not usage:
            usage = token_usage_from_counts(len(instruction), output_chars)
        turns.append(
            turn_record(
                run_dir=run_dir,
                phase="execution",
                agent=str(status.get("owner_role") or status.get("agent_profile") or "unknown_agent"),
                task_id=str(status.get("id") or status_path.stem.replace(".status", "")),
                status=str(status.get("status", "")),
                source_file=str(status_path.relative_to(run_dir).as_posix()),
                token_usage=usage,
                input_text=instruction,
                output_text="",
                warning_threshold=warning_threshold,
                context_guard=status.get("context_guard") if isinstance(status.get("context_guard"), dict) else None,
            )
        )
    return turns


def collect_meeting_turns(run_dir: Path, warning_threshold: int) -> list[dict[str, Any]]:
    transcript = run_dir / "ai_company" / "live_meeting_transcript.jsonl"
    if not transcript.exists():
        meeting = run_dir / "ai_company" / "meeting_decision.json"
        if not meeting.exists():
            return []
        try:
            payload = read_json(meeting)
        except json.JSONDecodeError:
            return []
        turns = []
        for item in payload.get("discussion_log", []):
            text = json.dumps(item, ensure_ascii=False, sort_keys=True)
            turns.append(
                turn_record(
                    run_dir=run_dir,
                    phase="meeting",
                    agent=str(item.get("role") or "meeting_agent"),
                    task_id=f"meeting-round-{item.get('round', 0)}",
                    status=str(item.get("decision_state", "")),
                    source_file=str(meeting.relative_to(run_dir).as_posix()),
                    input_text="",
                    output_text=text,
                    warning_threshold=warning_threshold,
                    context_guard=item.get("context_guard") if isinstance(item.get("context_guard"), dict) else None,
                )
            )
        return turns

    turns = []
    with transcript.open("r", encoding="utf-8", errors="ignore") as handle:
        lines = enumerate(handle, 1)
        for line_no, line in lines:
            if not line.strip():
                continue
            if len(line) > 2_000_000:
                line = line[:2_000_000]
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            text = str(event.get("response_excerpt") or event.get("error") or "")
            turns.append(
                turn_record(
                    run_dir=run_dir,
                    phase="meeting",
                    agent=str(event.get("role") or "meeting_agent"),
                    task_id=f"meeting-round-{event.get('round', 0)}-line-{line_no}",
                    status=str(event.get("parse_status", "")),
                    source_file=str(transcript.relative_to(run_dir).as_posix()),
                    token_usage=event.get("token_usage") if isinstance(event.get("token_usage"), dict) else token_usage_from_counts(0, len(text)),
                    input_text="",
                    output_text="",
                    transport=str(event.get("transport", "")),
                    attempt=int(event.get("attempt", 1) or 1),
                    duration_sec=float(event.get("duration_sec", 0) or 0),
                    warning_threshold=warning_threshold,
                )
            )
    return turns


def build_agent_token_ledger(run_dir: Path, warning_threshold: int = 24000) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    turns = [*collect_meeting_turns(run_dir, warning_threshold), *collect_status_turns(run_dir, warning_threshold)]
    by_agent: dict[str, dict[str, Any]] = {}
    by_phase: dict[str, dict[str, Any]] = defaultdict(lambda: {"phase": "", "turn_count": 0, "estimated_total_tokens": 0})
    for turn in turns:
        agent = turn["agent"]
        usage = turn["token_usage"]
        total = int(turn.get("effective_total_tokens", 0))
        entry = by_agent.setdefault(
            agent,
            {
                "agent": agent,
                "turn_count": 0,
                "estimated_input_tokens": 0,
                "estimated_output_tokens": 0,
                "estimated_total_tokens": 0,
                "provider_total_tokens": 0,
                "effective_total_tokens": 0,
                "max_turn_tokens": 0,
                "overflow_risk_turn_count": 0,
                "phases": {},
            },
        )
        entry["turn_count"] += 1
        entry["estimated_input_tokens"] += int(usage.get("estimated_input_tokens", 0))
        entry["estimated_output_tokens"] += int(usage.get("estimated_output_tokens", 0))
        entry["estimated_total_tokens"] += int(usage.get("estimated_total_tokens", 0))
        entry["provider_total_tokens"] += int(usage.get("provider_total_tokens") or 0)
        entry["effective_total_tokens"] += total
        entry["max_turn_tokens"] = max(entry["max_turn_tokens"], total)
        entry["overflow_risk_turn_count"] += 1 if turn.get("overflow_risk") else 0
        phase_entry = entry["phases"].setdefault(turn["phase"], {"turn_count": 0, "estimated_total_tokens": 0})
        phase_entry["turn_count"] += 1
        phase_entry["estimated_total_tokens"] += int(usage.get("estimated_total_tokens", 0))
        by_phase[turn["phase"]]["phase"] = turn["phase"]
        by_phase[turn["phase"]]["turn_count"] += 1
        by_phase[turn["phase"]]["estimated_total_tokens"] += int(usage.get("estimated_total_tokens", 0))

    agents = sorted(by_agent.values(), key=lambda item: (-item["estimated_total_tokens"], item["agent"]))
    top_agent = agents[0]["agent"] if agents else ""
    max_turn = max((int(turn.get("effective_total_tokens", 0)) for turn in turns), default=0)
    ledger = {
        "run_id": run_dir.name,
        "warning_threshold_tokens": warning_threshold,
        "estimation_method": "ceil(chars/4) unless provider usage exists",
        "total_estimated_agent_tokens": sum(int(agent["estimated_total_tokens"]) for agent in agents),
        "total_provider_agent_tokens": sum(int(agent["provider_total_tokens"]) for agent in agents),
        "total_effective_agent_tokens": sum(int(agent["effective_total_tokens"]) for agent in agents),
        "max_agent_turn_tokens": max_turn,
        "top_token_agent": top_agent,
        "overflow_risk_agent_count": sum(1 for agent in agents if int(agent["overflow_risk_turn_count"]) > 0),
        "agents": agents,
        "phases": sorted(by_phase.values(), key=lambda item: item["phase"]),
        "turns": turns,
    }
    out = run_dir / LEDGER_PATH
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
    return ledger


def main() -> None:
    parser = argparse.ArgumentParser(description="Build per-agent token ledger for an ai-company run.")
    parser.add_argument("run_dir")
    parser.add_argument("--warning-threshold", type=int, default=24000)
    args = parser.parse_args()
    ledger = build_agent_token_ledger(Path(args.run_dir), args.warning_threshold)
    print(json.dumps(ledger, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
