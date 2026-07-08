from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

ROOT = Path(__file__).resolve().parents[3]
RESULTS_ROOT = ROOT / "results" / "ai_company_task_harness"
ROSTER = ["meeting_coordinator", "planner_agent", "research_agent", "risk_reviewer", "decision_agent", "synthesis_agent", "reviewer_worker"]

app = FastAPI(title="Agent OS MVP", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))

def run_dirs() -> list[Path]:
    if not RESULTS_ROOT.exists():
        return []
    return sorted([p for p in RESULTS_ROOT.iterdir() if p.is_dir()], key=lambda p: p.stat().st_mtime, reverse=True)

def ai_dir(run_dir: Path) -> Path:
    return run_dir / "ai_company"

def report_for(run_dir: Path) -> dict[str, Any]:
    path = ai_dir(run_dir) / "task_harness_report.json"
    return load_json(path) if path.exists() else {}

def status_of(report: dict[str, Any]) -> str:
    value = str(report.get("overall_status") or "unknown").lower()
    return value if value in {"pass", "fail"} else "unknown"

def agent_board(run_dir: Path) -> dict[str, list[dict[str, Any]]]:
    board = {state: [] for state in ["running", "waiting", "done", "failed", "idle"]}
    meeting_path = ai_dir(run_dir) / "meeting_decision.json"
    assignments = load_json(meeting_path).get("task_assignments", []) if meeting_path.exists() else []
    assigned_roles = set()
    for item in assignments:
        role = item.get("owner_role") or item.get("role") or "unknown_agent"
        assigned_roles.add(role)
        status_path = run_dir / "results" / f"{item.get('task_id')}.status.json"
        status = load_json(status_path) if status_path.exists() else {}
        raw_state = str(status.get("status") or item.get("state") or "waiting").upper()
        if raw_state in {"SUCCESS", "ACCEPTED"}:
            state = "done"
        elif raw_state in {"FAILED", "ROUTER_ERROR", "OVERFLOW_DETECTED", "CHILD_TIMEOUT", "REPAIR_REQUIRED", "REPLAN_REQUIRED"}:
            state = "failed"
        elif raw_state in {"RUNNING", "IN_PROGRESS"}:
            state = "running"
        else:
            state = "waiting"
        board[state].append({"role": role, "task_id": item.get("task_id"), "state": state, "agent_profile": status.get("agent_profile") or item.get("agent_profile") or role, "policy": status.get("effective_policy_level") or "n/a", "note": status.get("verification_note") or ""})
    for role in ROSTER:
        if role not in assigned_roles:
            board["idle"].append({"role": role, "task_id": None, "state": "idle", "agent_profile": role, "policy": "n/a", "note": "not assigned in this run"})
    return board

def detail_payload(run_dir: Path) -> dict[str, Any]:
    report = report_for(run_dir)
    board = agent_board(run_dir)
    load = lambda name: load_json(ai_dir(run_dir) / name) if (ai_dir(run_dir) / name).exists() else {}
    return {"run_id": run_dir.name, "overall_status": status_of(report), "report": report, "agent_state_board": board, "agent_counts": {k: len(v) for k, v in board.items()}, "artifact_verify": load("artifact_verify_report.json"), "reviewer": load("reviewer_verdicts.json"), "claim_ledger": load("subagent_claim_ledger.json"), "watchdog": load("watchdog_report.json"), "memory_guard": load("main_agent_memory_guard_report.json")}

@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}

@app.get("/api/ai-company-monitor")
def monitor() -> dict[str, Any]:
    dirs = run_dirs()
    statuses = [status_of(report_for(run_dir)) for run_dir in dirs]
    counts = Counter(statuses)
    recent = [{"run_id": run_dir.name, "overall_status": status_of(report_for(run_dir)), "updated_at": run_dir.stat().st_mtime} for run_dir in dirs[:20]]
    selected = detail_payload(dirs[0]) if dirs else None
    return {"all_runs_summary": {"total_runs": len(dirs), "pass_count": counts.get("pass", 0), "fail_count": counts.get("fail", 0), "unknown_count": counts.get("unknown", 0), "status_breakdown": dict(counts)}, "selected_run_preview": selected, "recent_runs": recent, "latest_run": selected}

@app.get("/api/ai-company-monitor/runs/{run_id}")
def run_detail(run_id: str) -> dict[str, Any]:
    target = RESULTS_ROOT / run_id
    if not target.exists() or not target.is_dir():
        raise HTTPException(status_code=404, detail="run not found")
    return detail_payload(target)
