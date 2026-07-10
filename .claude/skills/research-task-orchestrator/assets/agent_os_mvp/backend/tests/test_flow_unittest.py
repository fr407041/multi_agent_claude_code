from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
import gc
import time

from app.db import init_db, get_db
from app.services.agent_engine import create_goal, execute_task, plan_tasks
from app.services.dashboard import collect_dashboard


class AgentOsFlowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "agent_os_test.db"
        os.environ["AGENT_OS_DB_PATH"] = str(self.db_path)
        init_db()

    def tearDown(self) -> None:
        os.environ.pop("AGENT_OS_DB_PATH", None)
        gc.collect()
        for _ in range(5):
            try:
                self.tempdir.cleanup()
                break
            except PermissionError:
                time.sleep(0.1)

    def test_end_to_end_goal_plan_execute_review_audit(self) -> None:
        with get_db() as connection:
            goal_id = create_goal(connection, "Ship MVP", "Create a working internal workflow demo")
            tasks = plan_tasks(connection, goal_id, "Ship MVP", "Create a working internal workflow demo")
            for task in tasks:
                execute_task(connection, task["id"])
            connection.commit()

            snapshot = collect_dashboard(connection)

        self.assertEqual(len(snapshot["goals"]), 1)
        self.assertEqual(snapshot["goals"][0]["status"], "completed")
        self.assertEqual(len(snapshot["tasks"]), 4)
        self.assertTrue(all(task["status"] == "done" for task in snapshot["tasks"]))
        self.assertGreaterEqual(len(snapshot["agent_runs"]), 5)
        self.assertEqual(len(snapshot["reviews"]), 4)
        self.assertEqual(len(snapshot["workbuddies"]), 4)
        self.assertTrue(all(item["status"] == "completed" for item in snapshot["workbuddies"]))
        self.assertGreaterEqual(len(snapshot["audit_logs"]), 14)


if __name__ == "__main__":
    unittest.main()
