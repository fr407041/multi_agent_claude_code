from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from agent_token_ledger import build_agent_token_ledger, token_usage_from_text


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class AgentTokenLedgerTests(unittest.TestCase):
    def test_builds_agent_summary_from_status_usage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run-token-ledger"
            raw = run_dir / "results" / "job-001.raw.txt"
            raw.parent.mkdir(parents=True)
            raw.write_text("short output", encoding="utf-8")
            write_json(
                run_dir / "results" / "job-001.status.json",
                {
                    "id": "job-001",
                    "status": "SUCCESS",
                    "owner_role": "research_agent",
                    "raw_file": str(raw),
                    "token_usage": token_usage_from_text("input words", "short output"),
                },
            )

            ledger = build_agent_token_ledger(run_dir, warning_threshold=100)
            ledger_file_exists = (run_dir / "ai_company" / "agent_token_ledger.json").exists()

            self.assertEqual(ledger["top_token_agent"], "research_agent")
            self.assertEqual(ledger["overflow_risk_agent_count"], 0)
            self.assertEqual(ledger["agents"][0]["turn_count"], 1)
            self.assertTrue(ledger_file_exists)

    def test_flags_large_turn_as_overflow_risk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run-token-pressure"
            raw = run_dir / "results" / "job-001.raw.txt"
            raw.parent.mkdir(parents=True)
            raw.write_text("x" * 2000, encoding="utf-8")
            write_json(
                run_dir / "results" / "job-001.status.json",
                {
                    "id": "job-001",
                    "status": "SUCCESS",
                    "owner_role": "synthesis_agent",
                    "raw_file": str(raw),
                },
            )

            ledger = build_agent_token_ledger(run_dir, warning_threshold=100)

        self.assertEqual(ledger["top_token_agent"], "synthesis_agent")
        self.assertEqual(ledger["overflow_risk_agent_count"], 1)
        self.assertGreaterEqual(ledger["max_agent_turn_tokens"], 100)

    def test_missing_raw_file_does_not_crash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run-token-missing"
            write_json(
                run_dir / "results" / "job-001.status.json",
                {
                    "id": "job-001",
                    "status": "ROUTER_ERROR",
                    "owner_role": "research_agent",
                    "raw_file": str(run_dir / "results" / "missing.raw.txt"),
                    "verification_note": "router failed",
                },
            )

            ledger = build_agent_token_ledger(run_dir, warning_threshold=100)

        self.assertEqual(ledger["agents"][0]["agent"], "research_agent")
        self.assertGreater(ledger["agents"][0]["estimated_total_tokens"], 0)

    def test_accumulates_live_meeting_agent_input_and_output_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run-live-meeting-token-ledger"
            transcript = run_dir / "ai_company" / "live_meeting_transcript.jsonl"
            transcript.parent.mkdir(parents=True)
            events = [
                {
                    "round": 1,
                    "role": "planner_agent",
                    "attempt": 1,
                    "transport": "claude_cli",
                    "duration_sec": 1.25,
                    "parse_status": "ok",
                    "response_excerpt": "bounded plan",
                    "token_usage": token_usage_from_text(
                        "planner input" * 10,
                        "planner output" * 10,
                        {"input_tokens": 40, "output_tokens": 20},
                    ),
                },
                {
                    "round": 1,
                    "role": "planner_agent",
                    "parse_status": "ok",
                    "response_excerpt": "second bounded plan",
                    "token_usage": token_usage_from_text("more input" * 10, "more output" * 10),
                },
            ]
            transcript.write_text("\n".join(json.dumps(item) for item in events), encoding="utf-8")

            ledger = build_agent_token_ledger(run_dir, warning_threshold=1000)

        planner = ledger["agents"][0]
        self.assertEqual(planner["agent"], "planner_agent")
        self.assertEqual(planner["turn_count"], 2)
        self.assertGreater(planner["estimated_input_tokens"], 0)
        self.assertGreater(planner["estimated_output_tokens"], 0)
        self.assertEqual(planner["provider_total_tokens"], 60)
        self.assertGreaterEqual(ledger["total_effective_agent_tokens"], 60)
        self.assertEqual(ledger["turns"][0]["transport"], "claude_cli")
        self.assertEqual(ledger["turns"][0]["duration_sec"], 1.25)
        self.assertEqual(ledger["phases"][0]["phase"], "meeting")

    def test_tracks_all_required_live_meeting_roles_separately(self) -> None:
        roles = ["meeting_coordinator", "planner_agent", "risk_reviewer", "decision_agent"]
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run-four-role-token-ledger"
            transcript = run_dir / "ai_company" / "live_meeting_transcript.jsonl"
            transcript.parent.mkdir(parents=True)
            events = [
                {
                    "round": 1,
                    "role": role,
                    "attempt": 1,
                    "transport": "claude_cli",
                    "parse_status": "ok",
                    "response_excerpt": f"{role} output",
                    "token_usage": token_usage_from_text(f"{role} input", f"{role} output"),
                }
                for role in roles
            ]
            transcript.write_text("\n".join(json.dumps(item) for item in events), encoding="utf-8")
            ledger = build_agent_token_ledger(run_dir, warning_threshold=1000)

        self.assertEqual({item["agent"] for item in ledger["agents"]}, set(roles))
        self.assertEqual(len(ledger["turns"]), 4)
        self.assertTrue(all(item["estimated_input_tokens"] > 0 for item in ledger["agents"]))
        self.assertTrue(all(item["estimated_output_tokens"] > 0 for item in ledger["agents"]))


if __name__ == "__main__":
    unittest.main()
