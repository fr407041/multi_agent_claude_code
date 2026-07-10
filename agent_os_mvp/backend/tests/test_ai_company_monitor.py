from __future__ import annotations

import gc
import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch

from app.db import get_db, init_db
from app.services.ai_company_monitor import _build_agent_state_board, _build_alerts, collect_ai_company_monitor, get_ai_company_run_detail


class AiCompanyMonitorStateBoardTest(unittest.TestCase):
    def test_package_integrity_failure_is_not_reported_as_model_failure(self) -> None:
        alerts = _build_alerts(
            {
                "kpis": {
                    "package_integrity": "PACKAGE_INTEGRITY_FAILED",
                    "failure_family_counts": {},
                }
            }
        )
        package_alert = next(item for item in alerts if item["type"] == "package_integrity")
        self.assertEqual("Package Integrity Failed", package_alert["title"])
        self.assertNotIn("model", package_alert["detail"].lower())

    def test_reviewer_worker_resolves_done_from_upstream_terminal_verdict(self) -> None:
        meeting = {
            "discussion_log": [
                {"role": "meeting_coordinator"},
                {"role": "planner_agent"},
                {"role": "risk_reviewer"},
                {"role": "decision_agent"},
            ],
            "task_assignments": [
                {
                    "task_id": "job-001",
                    "owner_role": "synthesis_agent",
                    "scope": ["summary.md"],
                    "depends_on": [],
                    "fallback_plan": "narrow scope",
                },
                {
                    "task_id": "job-001-review",
                    "owner_role": "reviewer_worker",
                    "scope": ["summary.md"],
                    "depends_on": ["job-001"],
                    "fallback_plan": "return repair",
                },
            ],
        }
        status_by_task = {
            "job-001": {"id": "job-001", "status": "SUCCESS", "owner_role": "synthesis_agent", "verification_note": "verified"}
        }
        verdict_by_task = {
            "job-001": {"task_id": "job-001", "verdict": "ACCEPTED", "review_status": "COMPLETE"}
        }

        roster, board, failures_by_agent, failure_summary = _build_agent_state_board(
            meeting, status_by_task, verdict_by_task
        )

        self.assertIn("reviewer_worker", roster)
        self.assertEqual([], [item["role"] for item in board["waiting"]])
        reviewer_row = next(item for item in board["done"] if item["role"] == "reviewer_worker")
        self.assertEqual("done", reviewer_row["state"])
        self.assertEqual([], failures_by_agent)
        self.assertEqual(0, failure_summary["false_success_blocked"])

    def test_idle_roles_are_preserved_when_not_in_meeting_or_assignments(self) -> None:
        meeting = {
            "discussion_log": [{"role": "meeting_coordinator"}],
            "task_assignments": [
                {
                    "task_id": "job-001",
                    "owner_role": "synthesis_agent",
                    "scope": ["summary.md"],
                    "depends_on": [],
                    "fallback_plan": "narrow scope",
                }
            ],
        }
        status_by_task = {"job-001": {"id": "job-001", "status": "RUNNING", "owner_role": "synthesis_agent"}}
        verdict_by_task = {}

        roster, board, failures_by_agent, failure_summary = _build_agent_state_board(
            meeting, status_by_task, verdict_by_task
        )

        self.assertIn("planner_agent", roster)
        self.assertIn("risk_reviewer", roster)
        self.assertIn("decision_agent", roster)
        idle_roles = {item["role"] for item in board["idle"]}
        self.assertTrue({"planner_agent", "risk_reviewer", "decision_agent", "reviewer_worker"}.issubset(idle_roles))
        running_roles = {item["role"] for item in board["running"]}
        self.assertIn("synthesis_agent", running_roles)
        self.assertEqual([], failures_by_agent)
        self.assertEqual(0, sum(failure_summary.values()))

    def test_failed_reviewer_dependency_is_reflected_as_failed_agent(self) -> None:
        meeting = {
            "discussion_log": [
                {"role": "meeting_coordinator"},
                {"role": "planner_agent"},
                {"role": "risk_reviewer"},
                {"role": "decision_agent"},
            ],
            "task_assignments": [
                {
                    "task_id": "job-001",
                    "owner_role": "synthesis_agent",
                    "scope": ["summary.md"],
                    "depends_on": [],
                    "fallback_plan": "narrow scope",
                },
                {
                    "task_id": "job-001-review",
                    "owner_role": "reviewer_worker",
                    "scope": ["summary.md"],
                    "depends_on": ["job-001"],
                    "fallback_plan": "return repair",
                },
            ],
        }
        status_by_task = {"job-001": {"id": "job-001", "status": "SUCCESS", "owner_role": "synthesis_agent"}}
        verdict_by_task = {
            "job-001": {"task_id": "job-001", "verdict": "FALSE_SUCCESS_BLOCKED", "review_status": "COMPLETE"}
        }

        _, board, failures_by_agent, failure_summary = _build_agent_state_board(meeting, status_by_task, verdict_by_task)

        reviewer_row = next(item for item in board["failed"] if item["role"] == "reviewer_worker")
        self.assertEqual("failed", reviewer_row["state"])
        reviewer_failure_row = next(item for item in failures_by_agent if item["role"] == "reviewer_worker")
        self.assertEqual("false_success_blocked", reviewer_failure_row["failures"][0]["failure_family"])
        self.assertEqual(2, failure_summary["false_success_blocked"])


class AiCompanyMonitorSummaryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tempdir.name, "agent_os_monitor_test.db")
        os.environ["AGENT_OS_DB_PATH"] = self.db_path
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

    def _insert_run(self, connection, run_id: str, status: str, started_at: str, roster_count: int = 6) -> None:
        board = {
            "running": [],
            "waiting": [],
            "done": [{"role": f"role-{index}"} for index in range(roster_count)],
            "failed": [],
            "idle": [],
        }
        payload = {
            "run_id": run_id,
            "spec_id": "monitor-test",
            "overall_status": status,
            "started_at": started_at,
            "goal": f"Goal for {run_id}",
            "decision_summary": f"Decision for {run_id}",
            "meeting_status": "complete",
            "roster": [f"role-{index}" for index in range(roster_count)],
            "roster_count": roster_count,
            "agent_state_board": board,
            "active_agents": [],
            "alerts": [],
            "failures_by_agent": [],
            "failure_summary": {
                "overflow": 0,
                "router": 0,
                "timeout": 0,
                "replan": 0,
                "repair": 0,
                "false_success_blocked": 0,
                "generic_failed": 0,
            },
            "final_result": {"artifact_score": 1.0, "artifact_checks": {}, "summary_markdown": ""},
        }
        connection.execute(
            """
            INSERT INTO ai_company_runs (
                run_id, spec_id, mode, overall_status, started_at, goal, decision_summary,
                meeting_status, artifact_score, active_agent_count, alerts_json, payload_json, synced_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                "monitor-test",
                "test",
                status,
                started_at,
                payload["goal"],
                payload["decision_summary"],
                payload["meeting_status"],
                1.0,
                0,
                "[]",
                json.dumps(payload),
                started_at,
            ),
        )

    def test_collect_monitor_uses_single_all_runs_summary_source(self) -> None:
        with get_db() as connection:
            for index in range(7):
                self._insert_run(connection, f"run-pass-{index:02d}", "pass", f"2026-07-05T20:{index:02d}:00")
            for index in range(5):
                self._insert_run(connection, f"run-fail-{index:02d}", "fail", f"2026-07-05T19:{index:02d}:00")
            self._insert_run(connection, "run-unknown-00", "mystery", "2026-07-05T18:00:00")
            connection.commit()

            with patch("app.services.ai_company_monitor.sync_ai_company_runs", lambda _: None):
                snapshot = collect_ai_company_monitor(connection)

        self.assertEqual(13, snapshot["all_runs_summary"]["total_runs"])
        self.assertEqual(7, snapshot["all_runs_summary"]["pass_count"])
        self.assertEqual(5, snapshot["all_runs_summary"]["fail_count"])
        self.assertEqual(1, snapshot["all_runs_summary"]["unknown_count"])
        self.assertEqual(13, snapshot["overview"]["total_runs"])
        self.assertEqual(7, snapshot["overview"]["pass_count"])
        self.assertEqual(5, snapshot["overview"]["fail_count"])
        self.assertEqual(1, snapshot["overview"]["unknown_count"])
        self.assertEqual(13, sum(snapshot["all_runs_summary"]["status_breakdown"].values()))
        self.assertEqual("run-unknown-00", snapshot["all_runs_summary"]["unknown_runs"][0]["run_id"])

    def test_run_detail_includes_selected_run_summary_and_roster_invariant(self) -> None:
        with get_db() as connection:
            payload = {
                "run_id": "run-detail-01",
                "spec_id": "monitor-test",
                "overall_status": "pass",
                "started_at": "2026-07-05T20:30:00",
                "goal": "Detailed goal",
                "decision_summary": "Detailed decision",
                "meeting_status": "complete",
                "roster": ["meeting_coordinator", "planner_agent", "synthesis_agent"],
                "roster_count": 3,
                "agent_state_board": {
                    "running": [],
                    "waiting": [{"role": "planner_agent"}],
                    "done": [{"role": "meeting_coordinator"}],
                    "failed": [{"role": "synthesis_agent"}],
                    "idle": [],
                },
                "active_agents": [{"role": "planner_agent"}],
                "alerts": [],
                "failures_by_agent": [{"role": "synthesis_agent", "failure_count": 1, "failures": []}],
                "failure_summary": {
                    "overflow": 0,
                    "router": 0,
                    "timeout": 0,
                    "replan": 0,
                    "repair": 0,
                    "false_success_blocked": 0,
                    "generic_failed": 1,
                },
                "final_result": {"artifact_score": 0.5, "artifact_checks": {}, "summary_markdown": ""},
            }
            connection.execute(
                """
                INSERT INTO ai_company_runs (
                    run_id, spec_id, mode, overall_status, started_at, goal, decision_summary,
                    meeting_status, artifact_score, active_agent_count, alerts_json, payload_json, synced_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "run-detail-01",
                    "monitor-test",
                    "test",
                    "pass",
                    "2026-07-05T20:30:00",
                    "Detailed goal",
                    "Detailed decision",
                    "complete",
                    0.5,
                    1,
                    "[]",
                    json.dumps(payload),
                    "2026-07-05T20:30:00",
                ),
            )
            connection.commit()

            with patch("app.services.ai_company_monitor.sync_ai_company_runs", lambda _: None):
                detail = get_ai_company_run_detail(connection, "run-detail-01")

        self.assertEqual(3, detail["roster_count"])
        self.assertEqual(3, sum(detail["selected_run_summary"][key] for key in [
            "done_agent_count",
            "waiting_agent_count",
            "running_agent_count",
            "failed_agent_count",
            "idle_agent_count",
        ]))
        self.assertEqual("pass", detail["selected_run_summary"]["overall_status"])


if __name__ == "__main__":
    unittest.main()
