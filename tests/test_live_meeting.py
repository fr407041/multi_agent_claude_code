from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from run_ai_company_meeting import build_meeting_decision


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def make_run_dir(root: Path) -> Path:
    run_dir = root / "run-live-meeting-test"
    write_json(
        run_dir / "summary.json",
        {
            "run_id": run_dir.name,
            "task": "Validate bounded provider-agnostic live meeting.",
            "agent_profile_mode": "strict",
            "expectations": {"meeting_status": "MEETING_READY"},
        },
    )
    write_json(
        run_dir / "plan.json",
        {
            "strategy": "Keep meeting compact and dispatch only bounded work.",
            "agent_profile_mode": "strict",
            "jobs": [
                {
                    "id": "job-001",
                    "title": "Inspect one bounded document",
                    "instruction": "Research bounded meeting evidence.",
                    "files": ["docs/AI_COMPANY_GOAL.zh-TW.md"],
                    "success_check": "Evidence is bounded.",
                }
            ],
        },
    )
    write_json(
        run_dir / "jobs" / "job-001.json",
        {
            "id": "job-001",
            "title": "Inspect one bounded document",
            "instruction": "Research bounded meeting evidence.",
            "files": ["docs/AI_COMPANY_GOAL.zh-TW.md"],
            "success_check": "Evidence is bounded.",
        },
    )
    return run_dir


class LiveMeetingTests(unittest.TestCase):
    def test_deterministic_meeting_is_default_and_backward_compatible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = make_run_dir(Path(tmp))
            with mock.patch.dict("os.environ", {}, clear=True):
                payload = build_meeting_decision(run_dir)

        self.assertEqual(payload["meeting_mode"], "deterministic")
        self.assertFalse(payload["live_meeting_used"])
        self.assertFalse(payload["live_degraded"])
        self.assertEqual(payload["meeting_status"], "MEETING_READY")
        self.assertGreaterEqual(len(payload["discussion_log"]), 4)

    def test_live_meeting_records_turns_and_keeps_assignments_valid(self) -> None:
        response = json.dumps(
            {
                "summary": "Role completed a compact bounded turn.",
                "proposed_actions": ["Keep scope bounded."],
                "risk_flags": [],
                "decision_state": "reviewed",
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = make_run_dir(Path(tmp))
            with mock.patch.dict(
                "os.environ",
                {
                    "AI_COMPANY_MEETING_MODE": "live",
                    "CLAUDE_MODEL_ALIAS": "provider-selected-model",
                },
                clear=True,
            ):
                with mock.patch("run_ai_company_meeting.call_ccr_for_live_turn", return_value=response):
                    payload = build_meeting_decision(run_dir)

            transcript = run_dir / "ai_company" / "live_meeting_transcript.jsonl"
            transcript_exists = transcript.exists()

        self.assertEqual(payload["meeting_mode"], "live")
        self.assertTrue(payload["live_meeting_used"])
        self.assertFalse(payload["live_degraded"])
        self.assertEqual(payload["live_turn_count"], 4)
        self.assertEqual(payload["meeting_status"], "MEETING_READY")
        self.assertTrue(transcript_exists)
        self.assertTrue(all(item.get("owner_role") for item in payload["task_assignments"]))
        self.assertTrue(all(len(item.get("scope", [])) <= 3 for item in payload["task_assignments"]))
        self.assertTrue(any(item.get("owner_role") == "reviewer_worker" for item in payload["task_assignments"]))

    def test_live_meeting_falls_back_when_model_returns_malformed_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = make_run_dir(Path(tmp))
            with mock.patch.dict(
                "os.environ",
                {
                    "AI_COMPANY_MEETING_MODE": "live",
                    "CLAUDE_MODEL_ALIAS": "provider-selected-model",
                },
                clear=True,
            ):
                with mock.patch("run_ai_company_meeting.call_ccr_for_live_turn", return_value="not json"):
                    payload = build_meeting_decision(run_dir)

        self.assertEqual(payload["meeting_mode"], "live")
        self.assertFalse(payload["live_meeting_used"])
        self.assertTrue(payload["live_degraded"])
        self.assertTrue(payload["degrade_reason"])
        self.assertEqual(payload["meeting_status"], "MEETING_READY")
        self.assertGreaterEqual(len(payload["discussion_log"]), 4)


if __name__ == "__main__":
    unittest.main()
