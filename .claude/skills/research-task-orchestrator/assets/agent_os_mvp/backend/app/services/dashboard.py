from __future__ import annotations

from sqlite3 import Connection

from app.services.agent_engine import fetch_all


def collect_dashboard(connection: Connection) -> dict:
    return {
        "goals": fetch_all(connection, "goals"),
        "tasks": fetch_all(connection, "tasks"),
        "agent_runs": fetch_all(connection, "agent_runs"),
        "reviews": fetch_all(connection, "reviews"),
        "workbuddies": fetch_all(connection, "workbuddies"),
        "audit_logs": fetch_all(connection, "audit_logs"),
    }
