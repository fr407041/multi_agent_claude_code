from __future__ import annotations

import json
import os
from pathlib import Path
import sys

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.db import get_db, get_db_path, init_db
from app.services.agent_engine import create_goal, plan_tasks, execute_task
from app.services.dashboard import collect_dashboard


def main() -> None:
    root = BACKEND_DIR
    smoke_db = root / "data" / "smoke_agent_os.db"
    os.environ["AGENT_OS_DB_PATH"] = str(smoke_db)

    if smoke_db.exists():
        smoke_db.unlink()

    init_db()
    with get_db() as connection:
        goal_id = create_goal(
            connection,
            "Prepare a tiny release note summary",
            "Use fixed-role agents and mocked tools to demonstrate planning, execution, review, and audit logging.",
        )
        tasks = plan_tasks(connection, goal_id, "Prepare a tiny release note summary", "Use fixed-role agents and mocked tools to demonstrate planning, execution, review, and audit logging.")
        for task in tasks:
            execute_task(connection, task["id"])
        connection.commit()
        snapshot = collect_dashboard(connection)

    print(f"DB path: {get_db_path()}")
    print(json.dumps({key: len(value) for key, value in snapshot.items()}, indent=2))


if __name__ == "__main__":
    main()
