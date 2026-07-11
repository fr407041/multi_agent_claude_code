from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

import sys

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from run_ai_company_meeting import (
    LiveProviderResult,
    assert_live_meeting_success,
    build_meeting_decision,
    call_claude_cli_for_live_turn,
    classify_live_error,
    derive_run_status_url,
    extract_claude_cli_result,
    extract_task_api_text,
    resolve_live_meeting_transport,
)


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
    def test_auto_transport_prefers_claude_cli_without_model_configuration(self) -> None:
        with mock.patch.dict("os.environ", {"AI_COMPANY_CLAUDE_BIN": "claude"}, clear=True):
            with mock.patch("run_ai_company_meeting.shutil.which", return_value="/usr/bin/claude"):
                transport, reason = resolve_live_meeting_transport()
        self.assertEqual(transport, "claude_cli")
        self.assertEqual(reason, "auto:claude_cli_available")

    def test_auto_transport_falls_back_to_ccr_when_claude_cli_is_unavailable(self) -> None:
        with mock.patch.dict("os.environ", {"AI_COMPANY_CLAUDE_BIN": "claude"}, clear=True):
            with mock.patch("run_ai_company_meeting.shutil.which", return_value=None):
                with mock.patch("run_ai_company_meeting.Path.is_file", return_value=False):
                    transport, reason = resolve_live_meeting_transport()
        self.assertEqual(transport, "ccr_http")
        self.assertEqual(reason, "auto:claude_cli_unavailable")

    def test_claude_cli_envelope_preserves_provider_usage(self) -> None:
        result = extract_claude_cli_result(
            json.dumps(
                {
                    "result": json.dumps({"summary": "ok", "proposed_actions": [], "risk_flags": [], "decision_state": "reviewed"}),
                    "usage": {"input_tokens": 20, "output_tokens": 10},
                }
            )
        )
        self.assertEqual(result.transport, "claude_cli")
        self.assertEqual(result.provider_usage["input_tokens"], 20)
        self.assertIn('"summary": "ok"', result.text)

    def test_claude_cli_turn_disables_tools_and_uses_compact_system_prompt(self) -> None:
        envelope = json.dumps(
            {
                "result": json.dumps(
                    {
                        "summary": "bounded",
                        "proposed_actions": [],
                        "risk_flags": [],
                        "decision_state": "reviewed",
                    }
                ),
                "usage": {"input_tokens": 10, "output_tokens": 5},
            }
        )
        completed = mock.Mock(returncode=0, stdout=envelope, stderr="")
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict("os.environ", {"AI_COMPANY_CLAUDE_BIN": "claude"}, clear=True):
                with mock.patch("run_ai_company_meeting.subprocess.run", return_value=completed) as run:
                    result = call_claude_cli_for_live_turn("prompt", Path(tmp))

        command = run.call_args.args[0]
        tools_index = command.index("--tools")
        self.assertEqual(command[tools_index + 1], "")
        self.assertIn("--system-prompt", command)
        self.assertEqual(result.text, json.loads(envelope)["result"])

    def test_live_task_api_helpers_are_provider_neutral(self) -> None:
        self.assertEqual(
            derive_run_status_url("http://127.0.0.1:8080/run-task", "run-123"),
            "http://127.0.0.1:8080/runs/run-123",
        )
        self.assertEqual(extract_task_api_text({"result_text": "ok"}), "ok")
        self.assertEqual(extract_task_api_text({"result": {"content": "nested"}}), "nested")

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
                    "AI_COMPANY_LIVE_MEETING_TRANSPORT": "ccr_http",
                    "CLAUDE_MODEL_ALIAS": "provider-selected-model",
                },
                clear=True,
            ):
                with mock.patch("run_ai_company_meeting.call_ccr_for_live_turn", return_value=response):
                    payload = build_meeting_decision(run_dir)

            transcript = run_dir / "ai_company" / "live_meeting_transcript.jsonl"
            transcript_exists = transcript.exists()
            transcript_events = [json.loads(line) for line in transcript.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(payload["meeting_mode"], "live")
        self.assertEqual(payload["requested_meeting_mode"], "live")
        self.assertTrue(payload["live_meeting_used"])
        self.assertFalse(payload["live_degraded"])
        self.assertEqual(payload["live_turn_count"], 4)
        self.assertEqual(payload["meeting_status"], "MEETING_READY")
        self.assertTrue(transcript_exists)
        self.assertTrue(all("token_usage" in item for item in transcript_events))
        self.assertTrue(all(item["token_usage"]["estimated_total_tokens"] > 0 for item in transcript_events))
        self.assertTrue(all(item.get("artifact_ref", "").startswith("ai_company/live_meeting_raw/") for item in transcript_events))
        self.assertTrue(all(item.get("transcript_ref") == "ai_company/live_meeting_transcript.jsonl" for item in transcript_events))
        self.assertTrue(all(item.get("transport") == "ccr_http" for item in transcript_events))
        self.assertEqual(payload["live_transport"], "ccr_http")
        self.assertEqual({item["role"] for item in transcript_events}, {"meeting_coordinator", "planner_agent", "risk_reviewer", "decision_agent"})
        self.assertTrue(all(item.get("owner_role") for item in payload["task_assignments"]))
        self.assertTrue(all(len(item.get("scope", [])) <= 3 for item in payload["task_assignments"]))
        self.assertTrue(any(item.get("owner_role") == "reviewer_worker" for item in payload["task_assignments"]))
        assert_live_meeting_success(payload)

    def test_live_meeting_falls_back_when_model_returns_malformed_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = make_run_dir(Path(tmp))
            with mock.patch.dict(
                "os.environ",
                {
                    "AI_COMPANY_MEETING_MODE": "live",
                    "AI_COMPANY_LIVE_MEETING_TRANSPORT": "ccr_http",
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
        self.assertEqual(payload["degrade_category"], "malformed_or_contract_mismatch")
        self.assertEqual(payload["meeting_status"], "MEETING_READY")
        self.assertGreaterEqual(len(payload["discussion_log"]), 4)
        with self.assertRaises(RuntimeError):
            assert_live_meeting_success(payload)

    def test_live_meeting_classifies_unauthorized_router_as_auth_not_model_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = make_run_dir(Path(tmp))
            unauthorized = urllib.error.HTTPError(
                url="http://127.0.0.1:3456/v1/messages",
                code=401,
                msg="Unauthorized",
                hdrs=None,
                fp=None,
            )
            with mock.patch.dict(
                "os.environ",
                {
                    "AI_COMPANY_MEETING_MODE": "live",
                    "AI_COMPANY_LIVE_MEETING_TRANSPORT": "ccr_http",
                    "CLAUDE_MODEL_ALIAS": "provider-selected-model",
                },
                clear=True,
            ):
                with mock.patch("run_ai_company_meeting.call_ccr_for_live_turn", side_effect=unauthorized):
                    payload = build_meeting_decision(run_dir)

            transcript = run_dir / "ai_company" / "live_meeting_transcript.jsonl"
            first_event = json.loads(transcript.read_text(encoding="utf-8").splitlines()[0])

        self.assertEqual(payload["degrade_category"], "router_auth_unauthorized")
        self.assertEqual(first_event["failure_category"], "router_auth_unauthorized")
        self.assertTrue(payload["live_degraded"])
        self.assertFalse(payload["live_meeting_used"])
        self.assertEqual(classify_live_error(unauthorized), "router_auth_unauthorized")

    def test_transport_resource_exhaustion_retries_twice_then_succeeds(self) -> None:
        response = LiveProviderResult(
            text=json.dumps(
                {
                    "summary": "Recovered after bounded retries.",
                    "proposed_actions": ["Continue."],
                    "risk_flags": [],
                    "decision_state": "reviewed",
                }
            ),
            transport="ccr_http",
            provider_usage={},
        )
        resource_error = RuntimeError("[WinError 10055] buffer space or queue was full")
        side_effects = [resource_error, resource_error, response, response, response, response]
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = make_run_dir(Path(tmp))
            with mock.patch.dict(
                "os.environ",
                {
                    "AI_COMPANY_MEETING_MODE": "live",
                    "AI_COMPANY_LIVE_MEETING_TRANSPORT": "ccr_http",
                    "CLAUDE_MODEL_ALIAS": "provider-selected-model",
                },
                clear=True,
            ):
                with mock.patch("run_ai_company_meeting.call_live_provider_for_turn", side_effect=side_effects) as provider_call:
                    with mock.patch("run_ai_company_meeting.time.sleep"):
                        payload = build_meeting_decision(run_dir)

        self.assertTrue(payload["live_meeting_used"])
        self.assertFalse(payload["live_degraded"])
        self.assertEqual(provider_call.call_count, 6)

    def test_contract_repair_is_attempted_only_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = make_run_dir(Path(tmp))
            with mock.patch.dict(
                "os.environ",
                {
                    "AI_COMPANY_MEETING_MODE": "live",
                    "AI_COMPANY_LIVE_MEETING_TRANSPORT": "ccr_http",
                    "CLAUDE_MODEL_ALIAS": "provider-selected-model",
                },
                clear=True,
            ):
                with mock.patch("run_ai_company_meeting.call_ccr_for_live_turn", return_value="not json") as provider_call:
                    payload = build_meeting_decision(run_dir)

        self.assertEqual(provider_call.call_count, 2)
        self.assertTrue(payload["live_degraded"])
        self.assertEqual(payload["degrade_category"], "malformed_or_contract_mismatch")

    def test_auto_meeting_stays_deterministic_for_simple_single_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = make_run_dir(Path(tmp))
            summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
            summary["agent_profile_mode"] = "simple"
            (run_dir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
            plan = json.loads((run_dir / "plan.json").read_text(encoding="utf-8"))
            plan["agent_profile_mode"] = "simple"
            (run_dir / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
            with mock.patch.dict("os.environ", {"AI_COMPANY_MEETING_MODE": "auto"}, clear=True):
                payload = build_meeting_decision(run_dir)

        self.assertEqual(payload["requested_meeting_mode"], "auto")
        self.assertEqual(payload["meeting_mode"], "deterministic")
        self.assertEqual(payload["meeting_mode_reason"], "auto:simple_single_agent")

    def test_auto_meeting_promotes_strict_run_to_live(self) -> None:
        response = json.dumps(
            {
                "summary": "Strict role completed a compact turn.",
                "proposed_actions": ["Keep strict profile attached."],
                "risk_flags": [],
                "decision_state": "reviewed",
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = make_run_dir(Path(tmp))
            with mock.patch.dict(
                "os.environ",
                {
                    "AI_COMPANY_MEETING_MODE": "auto",
                    "AI_COMPANY_LIVE_MEETING_TRANSPORT": "ccr_http",
                    "CLAUDE_MODEL_ALIAS": "provider-selected-model",
                },
                clear=True,
            ):
                with mock.patch("run_ai_company_meeting.call_ccr_for_live_turn", return_value=response):
                    payload = build_meeting_decision(run_dir)

        self.assertEqual(payload["requested_meeting_mode"], "auto")
        self.assertEqual(payload["meeting_mode"], "live")
        self.assertEqual(payload["meeting_mode_reason"], "auto:risk_or_multi_agent_trigger")
        self.assertTrue(payload["live_meeting_used"])


if __name__ == "__main__":
    unittest.main()
