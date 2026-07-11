from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.session_store import append_event, append_message, ensure_session, init_session_store, session_dashboard, write_session_artifact
from app.routers.api import collect_hook_event, dashboard_config
from app.schemas import HookEventRequest


class SessionStoreTests(unittest.TestCase):
    def test_sqlite_session_messages_events_and_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {"AGENT_OS_DB_PATH": str(Path(tmp) / "agent.db"), "AGENT_OS_SESSION_ROOT": str(Path(tmp) / "sessions"), "AGENT_OS_DATABASE_URL": ""},
            clear=False,
        ):
            init_session_store()
            ensure_session("session-1")
            append_message("session-1", "user", "hello")
            artifact = write_session_artifact("session-1", "turn.json", {"ok": True})
            append_event("session-1", "turn_completed", "main_agent", {"status": "completed", "artifact": artifact})
            payload = session_dashboard("session-1")
        self.assertEqual(payload["session"]["status"], "completed")
        self.assertEqual(payload["messages"][0]["content"], "hello")
        self.assertEqual(payload["events"][0]["event_type"], "turn_completed")
        self.assertEqual(Path(artifact).name, "turn.json")

    def test_framework_hook_and_ui_config_are_runtime_controlled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {"AGENT_OS_DB_PATH": str(Path(tmp) / "agent.db"), "AGENT_OS_DATABASE_URL": "", "AGENT_OS_SHOW_CHAT": "0"},
            clear=False,
        ):
            init_session_store()
            response = collect_hook_event(HookEventRequest(session_id="hook-session", event_type="task_started", agent_role="planner", payload={"status": "running"}), None)
            payload = session_dashboard("hook-session")
            config = dashboard_config()
        self.assertTrue(response["accepted"])
        self.assertEqual(payload["events"][0]["event_type"], "task_started")
        self.assertFalse(config["show_chat"])


if __name__ == "__main__":
    unittest.main()
