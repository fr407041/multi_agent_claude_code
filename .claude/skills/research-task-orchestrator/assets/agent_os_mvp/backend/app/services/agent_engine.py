from __future__ import annotations

import json
from datetime import datetime, timezone
from sqlite3 import Connection, Row

from app.schemas import AgentRole
from app.services.tools import run_tool_for_role


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def row_to_dict(row: Row) -> dict:
    return dict(row)


def log_audit(
    connection: Connection,
    actor: str,
    action: str,
    details: str,
    goal_id: int | None = None,
    task_id: int | None = None,
) -> None:
    connection.execute(
        """
        INSERT INTO audit_logs (goal_id, task_id, actor, action, details, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (goal_id, task_id, actor, action, details, utc_now()),
    )


def create_goal(connection: Connection, title: str, description: str) -> int:
    now = utc_now()
    cursor = connection.execute(
        """
        INSERT INTO goals (title, description, status, created_at, updated_at)
        VALUES (?, ?, 'planned', ?, ?)
        """,
        (title, description, now, now),
    )
    goal_id = cursor.lastrowid
    log_audit(connection, "user", "goal_created", description, goal_id=goal_id)
    return int(goal_id)


def planner_breakdown(goal_title: str, goal_description: str) -> list[dict]:
    seed = f"{goal_title}. {goal_description}".strip()
    return [
        {
            "title": "Clarify constraints and gather internal references",
            "description": f"Research the goal context, assumptions, and internal dependencies for: {seed}",
            "agent_role": AgentRole.research.value,
            "priority": 1,
        },
        {
            "title": "Implement the smallest useful solution slice",
            "description": f"Build an initial working implementation aligned to: {seed}",
            "agent_role": AgentRole.dev.value,
            "priority": 2,
        },
        {
            "title": "Validate acceptance criteria and edge cases",
            "description": f"Check core behavior, risks, and missing acceptance criteria for: {seed}",
            "agent_role": AgentRole.qa.value,
            "priority": 3,
        },
        {
            "title": "Review outcomes and readiness",
            "description": f"Review whether the delivered work satisfies the original goal: {seed}",
            "agent_role": AgentRole.reviewer.value,
            "priority": 4,
        },
    ]


def planner_workbuddy_map() -> dict[str, tuple[str, str]]:
    return {
        AgentRole.research.value: (
            AgentRole.dev.value,
            "Research hands validated findings to Dev so implementation starts with the right internal context.",
        ),
        AgentRole.dev.value: (
            AgentRole.qa.value,
            "Dev pairs with QA to turn implementation risks into concrete acceptance checks.",
        ),
        AgentRole.qa.value: (
            AgentRole.reviewer.value,
            "QA and Reviewer align on release readiness, residual risk, and whether the goal is actually satisfied.",
        ),
        AgentRole.reviewer.value: (
            AgentRole.planner.value,
            "Reviewer feeds final lessons back to Planner so the next goal starts from better operating context.",
        ),
    }


def plan_tasks(connection: Connection, goal_id: int, title: str, description: str) -> list[dict]:
    plan = planner_breakdown(title, description)
    planner_output = json.dumps(plan, ensure_ascii=True)
    connection.execute(
        """
        INSERT INTO agent_runs (goal_id, task_id, agent_role, tool_name, status, input_payload, output_payload, created_at)
        VALUES (?, NULL, ?, ?, ?, ?, ?, ?)
        """,
        (
            goal_id,
            AgentRole.planner.value,
            "obsidian_write_draft",
            "completed",
            description,
            planner_output,
            utc_now(),
        ),
    )
    log_audit(
        connection,
        AgentRole.planner.value,
        "task_plan_created",
        planner_output,
        goal_id=goal_id,
    )

    created = []
    buddy_map = planner_workbuddy_map()
    now = utc_now()
    for item in plan:
        cursor = connection.execute(
            """
            INSERT INTO tasks (goal_id, title, description, agent_role, status, priority, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)
            """,
            (
                goal_id,
                item["title"],
                item["description"],
                item["agent_role"],
                item["priority"],
                now,
                now,
            ),
        )
        task_id = int(cursor.lastrowid)
        item["id"] = task_id
        created.append(item)
        log_audit(
            connection,
            AgentRole.planner.value,
            "task_created",
            item["description"],
            goal_id=goal_id,
            task_id=task_id,
        )
        buddy_role, buddy_note = buddy_map[item["agent_role"]]
        connection.execute(
            """
            INSERT INTO workbuddies (goal_id, task_id, primary_role, buddy_role, collaboration_note, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'suggested', ?, ?)
            """,
            (
                goal_id,
                task_id,
                item["agent_role"],
                buddy_role,
                buddy_note,
                now,
                now,
            ),
        )
        log_audit(
            connection,
            AgentRole.planner.value,
            "workbuddy_assigned",
            f"{item['agent_role']} paired with {buddy_role}: {buddy_note}",
            goal_id=goal_id,
            task_id=task_id,
        )

    connection.execute(
        "UPDATE goals SET status = 'tasked', updated_at = ? WHERE id = ?",
        (utc_now(), goal_id),
    )
    return created


def _choose_tool_for_role(role: str) -> str:
    mapping = {
        AgentRole.research.value: "tkms_search",
        AgentRole.dev.value: "gitlab_create_mr_draft",
        AgentRole.qa.value: "oracle_query_readonly",
        AgentRole.reviewer.value: "obsidian_write_draft",
    }
    return mapping.get(role, "obsidian_write_draft")


def execute_task(connection: Connection, task_id: int) -> dict:
    task = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if task is None:
        raise ValueError(f"Task {task_id} not found")

    task_data = row_to_dict(task)
    role = task_data["agent_role"]
    tool_name = _choose_tool_for_role(role)
    output = run_tool_for_role(role, tool_name, task_data["description"])
    buddy = connection.execute(
        "SELECT * FROM workbuddies WHERE task_id = ?",
        (task_id,),
    ).fetchone()
    buddy_summary = ""
    if buddy is not None:
        buddy_summary = f" Workbuddy: {buddy['buddy_role']} | {buddy['collaboration_note']}"
        connection.execute(
            """
            UPDATE workbuddies
            SET status = 'engaged', updated_at = ?
            WHERE task_id = ?
            """,
            (utc_now(), task_id),
        )

    connection.execute(
        """
        INSERT INTO agent_runs (goal_id, task_id, agent_role, tool_name, status, input_payload, output_payload, created_at)
        VALUES (?, ?, ?, ?, 'completed', ?, ?, ?)
        """,
        (
            task_data["goal_id"],
            task_id,
            role,
            tool_name,
            task_data["description"],
            output + buddy_summary,
            utc_now(),
        ),
    )
    connection.execute(
        """
        UPDATE tasks
        SET status = 'done', result_summary = ?, updated_at = ?
        WHERE id = ?
        """,
        (output + buddy_summary, utc_now(), task_id),
    )
    log_audit(connection, role, "task_executed", output + buddy_summary, goal_id=task_data["goal_id"], task_id=task_id)

    verdict = "final_signoff" if role == AgentRole.reviewer.value else "approved_with_notes"
    if "Mock" not in output:
        verdict = "needs_followup"
    notes = f"Reviewer checked output from {role}: {output}"
    connection.execute(
        """
        INSERT INTO reviews (goal_id, task_id, reviewer_role, verdict, notes, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            task_data["goal_id"],
            task_id,
            AgentRole.reviewer.value,
            verdict,
            notes,
            utc_now(),
        ),
    )
    log_audit(
        connection,
        AgentRole.reviewer.value,
        "task_reviewed",
        notes,
        goal_id=task_data["goal_id"],
        task_id=task_id,
    )
    if buddy is not None:
        connection.execute(
            """
            UPDATE workbuddies
            SET status = 'completed', updated_at = ?
            WHERE task_id = ?
            """,
            (utc_now(), task_id),
        )
        log_audit(
            connection,
            buddy["buddy_role"],
            "workbuddy_closed",
            f"Buddy loop closed for task {task_id}",
            goal_id=task_data["goal_id"],
            task_id=task_id,
        )

    remaining = connection.execute(
        "SELECT COUNT(*) AS count FROM tasks WHERE goal_id = ? AND status != 'done'",
        (task_data["goal_id"],),
    ).fetchone()
    if remaining and int(remaining["count"]) == 0:
        connection.execute(
            "UPDATE goals SET status = 'completed', updated_at = ? WHERE id = ?",
            (utc_now(), task_data["goal_id"]),
        )
        log_audit(connection, "system", "goal_completed", f"Goal {task_data['goal_id']} completed", goal_id=task_data["goal_id"])

    return {
        "task_id": task_id,
        "tool_name": tool_name,
        "output": output,
    }


def fetch_all(connection: Connection, table: str) -> list[dict]:
    rows = connection.execute(f"SELECT * FROM {table} ORDER BY id DESC").fetchall()
    return [row_to_dict(row) for row in rows]
