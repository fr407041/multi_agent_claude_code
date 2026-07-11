from __future__ import annotations

import os

from fastapi import APIRouter, Header, HTTPException

from app.db import get_db
from app.schemas import ChatRequest, ChatResponse, DashboardResponse, GoalCreate, GoalCreatedResponse, HookEventRequest
from app.services.ai_company_monitor import collect_ai_company_monitor, get_ai_company_run_detail
from app.services.agent_engine import create_goal, execute_task, plan_tasks
from app.services.dashboard import collect_dashboard
from app.services.claude_session import run_chat
from app.services.session_store import append_event, ensure_session, session_dashboard


router = APIRouter(prefix="/api", tags=["agent-os"])


@router.post("/v1/chat", response_model=ChatResponse)
def chat(payload: ChatRequest):
    try:
        return run_chat(payload.prompt, payload.session_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/v1/dashboard/config")
def dashboard_config():
    def enabled(name: str, default: bool = True) -> bool:
        return os.getenv(name, "1" if default else "0").strip().lower() not in {"0", "false", "no", "off"}
    return {
        "show_progress_bar": enabled("AGENT_OS_SHOW_PROGRESS_BAR"),
        "show_agent_logs": enabled("AGENT_OS_SHOW_AGENT_LOGS"),
        "show_artifacts": enabled("AGENT_OS_SHOW_ARTIFACTS"),
        "show_chat": enabled("AGENT_OS_SHOW_CHAT"),
        "event_policy": "explicit_messages_tools_status_artifacts_only",
    }


@router.get("/v1/dashboard/{session_id}")
def session_detail(session_id: str):
    payload = session_dashboard(session_id)
    if payload["session"] is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    return payload


@router.post("/v1/hooks/events")
def collect_hook_event(payload: HookEventRequest, x_agent_os_hook_token: str | None = Header(default=None)):
    expected = os.getenv("AGENT_OS_HOOK_TOKEN", "").strip()
    if expected and x_agent_os_hook_token != expected:
        raise HTTPException(status_code=401, detail="Invalid hook token")
    ensure_session(payload.session_id)
    append_event(payload.session_id, payload.event_type, payload.agent_role, payload.payload)
    return {"accepted": True, "session_id": payload.session_id}


@router.post("/goals", response_model=GoalCreatedResponse)
def create_goal_endpoint(payload: GoalCreate):
    with get_db() as connection:
        goal_id = create_goal(connection, payload.title, payload.description)
        plan_tasks(connection, goal_id, payload.title, payload.description)
        connection.commit()

        goal = connection.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
        tasks = connection.execute(
            "SELECT * FROM tasks WHERE goal_id = ? ORDER BY priority ASC, id ASC",
            (goal_id,),
        ).fetchall()
        return {
            "goal": dict(goal),
            "tasks": [dict(task) for task in tasks],
        }


@router.post("/tasks/{task_id}/run")
def run_task_endpoint(task_id: int):
    with get_db() as connection:
        try:
            result = execute_task(connection, task_id)
            connection.commit()
            return result
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/goals/{goal_id}/run-all")
def run_goal_endpoint(goal_id: int):
    with get_db() as connection:
        goal = connection.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
        if goal is None:
            raise HTTPException(status_code=404, detail=f"Goal {goal_id} not found")
        pending_tasks = connection.execute(
            "SELECT id FROM tasks WHERE goal_id = ? AND status != 'done' ORDER BY priority ASC, id ASC",
            (goal_id,),
        ).fetchall()
        results = [execute_task(connection, row["id"]) for row in pending_tasks]
        connection.commit()
        return {"goal_id": goal_id, "executed": results}


@router.get("/dashboard", response_model=DashboardResponse)
def dashboard():
    with get_db() as connection:
        return collect_dashboard(connection)


@router.get("/ai-company-monitor")
def ai_company_monitor():
    with get_db() as connection:
        return collect_ai_company_monitor(connection)


@router.get("/ai-company-monitor/runs/{run_id}")
def ai_company_run_detail(run_id: str):
    with get_db() as connection:
        try:
            return get_ai_company_run_detail(connection, run_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"Run {run_id} not found") from exc


@router.post("/demo/seed")
def seed_demo():
    demo_goal = GoalCreate(
        title="Prepare an internal release readiness summary",
        description="Create a small internal summary with research findings, implementation notes, QA concerns, and reviewer feedback.",
    )
    response = create_goal_endpoint(demo_goal)
    task_ids = [task["id"] for task in response["tasks"]]
    with get_db() as connection:
        results = [execute_task(connection, task_id) for task_id in task_ids]
        connection.commit()
    return {"goal_id": response["goal"]["id"], "executed": results}
