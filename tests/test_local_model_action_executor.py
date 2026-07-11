from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys as _sys

if str(ROOT := Path(__file__).resolve().parent.parent) not in _sys.path:
    _sys.path.insert(0, str(ROOT))

from scripts import run_local_model_action_executor as executor


SCRIPT = ROOT / "scripts" / "run_local_model_action_executor.py"


class LocalModelActionExecutorTests(unittest.TestCase):
    def test_truncated_task_output_has_specific_failure_category(self) -> None:
        with self.assertRaises(executor.ManifestTransportError) as caught:
            executor.extract_action_manifest_from_task_output('prefix\n{"actions":[{"type":"write_file","path":"x"')
        self.assertEqual(caught.exception.category, "manifest_truncated")
        self.assertIn('"actions"', caught.exception.raw_response)

    def test_provider_neutral_task_api_helpers(self) -> None:
        self.assertEqual(executor.extract_task_api_text({"result": {"content": "manifest"}}), "manifest")
        self.assertEqual(
            executor.derive_task_status_url("http://127.0.0.1:8080/run-task", "run-123"),
            "http://127.0.0.1:8080/runs/run-123",
        )
        noisy = 'verify output\n{"overall_status":"pass"}\n{"actions":[{"type":"finish","summary":"ok","artifacts":[]}]}\n'
        extracted = json.loads(executor.extract_action_manifest_from_task_output(noisy))
        self.assertEqual(extracted["actions"][0]["type"], "finish")

    def run_executor(self, manifest: dict, task: str = "create a probe file") -> subprocess.CompletedProcess[str]:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        root = Path(temp_dir.name)
        manifest_path = root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--task",
                task,
                "--manifest-file",
                str(manifest_path),
                "--out-root",
                str(root / "runs"),
                "--run-id",
                "run-test-action-executor",
                "--allowed-commands",
                f"{Path(sys.executable).name},python,python3",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_executes_allowlisted_manifest_and_emits_artifacts(self) -> None:
        proc = self.run_executor(
            {
                "actions": [
                    {"type": "write_file", "path": "probe.txt", "content": "ok\n"},
                    {"type": "run_command", "command": [sys.executable, "-c", "from pathlib import Path; assert Path('probe.txt').read_text() == 'ok\\n'"]},
                    {"type": "finish", "summary": "probe artifact created", "artifacts": ["probe.txt"]},
                ]
            }
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        report = json.loads(proc.stdout)
        run_dir = Path(report["run_dir"])
        self.assertEqual(report["overall_status"], "pass")
        self.assertTrue((run_dir / "worktree" / "probe.txt").exists())
        self.assertTrue((run_dir / "ai_company" / "task_harness_report.json").exists())
        self.assertTrue((run_dir / "results" / "job-001.status.json").exists())
        self.assertIn("return_code=0", (run_dir / "results" / "job-001.exec.log").read_text(encoding="utf-8"))

    def test_failed_command_persists_stdout_and_stderr(self) -> None:
        proc = self.run_executor(
            {
                "actions": [
                    {
                        "type": "run_command",
                        "command": [sys.executable, "-c", "import sys; print('visible-out'); print('visible-err', file=sys.stderr); raise SystemExit(7)"],
                    }
                ]
            }
        )
        self.assertNotEqual(proc.returncode, 0)
        report = json.loads(proc.stdout)
        exec_log = (Path(report["run_dir"]) / "results" / "job-001.exec.log").read_text(encoding="utf-8")
        self.assertIn("return_code=7", exec_log)
        self.assertIn("visible-out", exec_log)
        self.assertIn("visible-err", exec_log)

    def test_staged_execution_generates_bounded_artifact_batches(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        root = Path(temp_dir.name)
        responses = [
            json.dumps({"actions": [
                {"type": "write_file", "path": "worker.py", "content": "print('ok')\n"},
                {"type": "write_file", "path": "output.json", "content": "{\"status\": \"pass\"}\n"},
            ]}),
            json.dumps({"actions": [
                {"type": "write_file", "path": "dashboard.html", "content": "<h1>pass</h1>"},
                {"type": "write_file", "path": "report.md", "content": "# Pass\n"},
            ]}),
        ]
        with mock.patch.object(executor, "call_ccr_for_manifest", side_effect=responses):
            with mock.patch.object(sys, "argv", [
                "run_local_model_action_executor.py", "--task", "create four bounded artifacts",
                "--out-root", str(root / "runs"), "--run-id", "run-staged",
                "--expected-artifact", "worker.py", "--expected-artifact", "output.json",
                "--expected-artifact", "report.md", "--expected-artifact", "dashboard.html",
                "--execution-strategy", "auto", "--max-repair-rounds", "0",
            ]):
                code = executor.main()
        self.assertEqual(code, 0)
        report = json.loads((root / "runs" / "run-staged" / "ai_company" / "task_harness_report.json").read_text())
        self.assertEqual(report["kpis"]["execution_strategy"], "staged")
        self.assertEqual(report["kpis"]["stage_count"], 2)
        self.assertEqual(report["kpis"]["attempt_count"], 2)

    def test_static_derived_artifact_is_blocked(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        root = Path(temp_dir.name)
        manifest = root / "manifest.json"
        manifest.write_text(json.dumps({"actions": [
            {"type": "write_file", "path": "kpi.json", "content": "{\"score\": 100}"},
            {"type": "finish", "summary": "static", "artifacts": ["kpi.json"]},
        ]}), encoding="utf-8")
        proc = subprocess.run([
            sys.executable, str(SCRIPT), "--task", "derive KPI", "--manifest-file", str(manifest),
            "--expected-artifact", "kpi.json", "--derived-artifact", "kpi.json",
            "--out-root", str(root / "runs"), "--run-id", "run-static-derived",
        ], cwd=ROOT, text=True, capture_output=True, check=False)
        self.assertNotEqual(proc.returncode, 0)
        report = json.loads(proc.stdout)
        self.assertEqual(report["kpis"]["failure_category"], "artifact_provenance_failed")

    def test_rejects_path_traversal(self) -> None:
        proc = self.run_executor({"actions": [{"type": "write_file", "path": "../escape.txt", "content": "bad"}]})
        self.assertNotEqual(proc.returncode, 0)
        report = json.loads(proc.stdout)
        self.assertEqual(report["overall_status"], "fail")
        self.assertIn("escapes run worktree", report.get("error", ""))

    def test_rejects_non_allowlisted_command(self) -> None:
        proc = self.run_executor({"actions": [{"type": "run_command", "command": ["powershell", "-Command", "echo bad"]}]})
        self.assertNotEqual(proc.returncode, 0)
        report = json.loads(proc.stdout)
        self.assertEqual(report["overall_status"], "fail")
        self.assertEqual(report["kpis"]["artifact_pass_rate"], 0.0)

    def test_non_allowlisted_command_preserves_partial_action_log(self) -> None:
        proc = self.run_executor(
            {
                "actions": [
                    {"type": "write_file", "path": "kept.txt", "content": "kept\n"},
                    {"type": "run_command", "command": ["powershell", "-Command", "echo rejected"]},
                ]
            }
        )
        self.assertNotEqual(proc.returncode, 0)
        report = json.loads(proc.stdout)
        actions = json.loads((Path(report["run_dir"]) / "results" / "job-001.action_log.json").read_text())["actions"]
        self.assertEqual([item["status"] for item in actions], ["ok", "failed"])
        self.assertIn("not allowlisted", actions[-1]["error"])

    def test_stage_local_provenance_repair_reopens_owning_stage(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        root = Path(temp_dir.name)
        responses = [
            json.dumps({"actions": [{"type": "write_file", "path": "source.py", "content": "from pathlib import Path\nPath('derived.json').write_text('{\\\"status\\\": \\\"pass\\\"}')\n"}]}),
            json.dumps({"actions": [{"type": "write_file", "path": "derived.json", "content": "{\"status\": \"pass\"}"}]}),
            json.dumps({"actions": [{"type": "run_command", "command": [sys.executable, "source.py"]}]}),
            json.dumps({"actions": [{"type": "write_file", "path": "report.md", "content": "# Report\n"}]}),
        ]
        calls: list[str] = []

        def fake_call(task: str, timeout_sec: int, repair_feedback: str = "") -> str:
            calls.append(repair_feedback)
            return responses[len(calls) - 1]

        with mock.patch.object(executor, "call_ccr_for_manifest", side_effect=fake_call):
            with mock.patch.object(sys, "argv", [
                "run_local_model_action_executor.py", "--task", "build derived report",
                "--out-root", str(root / "runs"), "--run-id", "run-stage-provenance-repair",
                "--expected-artifact", "source.py", "--expected-artifact", "derived.json",
                "--expected-artifact", "report.md", "--derived-artifact", "derived.json",
                "--allowed-commands", Path(sys.executable).name,
                "--execution-strategy", "staged", "--stage-artifact-limit", "1", "--max-repair-rounds", "1",
            ]):
                code = executor.main()
        self.assertEqual(code, 0)
        self.assertIn("failed_derived_artifacts", calls[2])
        self.assertIn("derived.json", calls[2])
        report = json.loads((root / "runs" / "run-stage-provenance-repair" / "ai_company" / "task_harness_report.json").read_text())
        self.assertEqual(report["overall_status"], "pass")
        self.assertEqual(report["kpis"]["repair_rounds_used"], 1)

    def test_runner_owned_domain_verdict_blocks_self_reported_pass(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        root = Path(temp_dir.name)
        actual = root / "actual.json"
        expected = root / "expected.json"
        actual.write_text(json.dumps({"status": "pass", "parsed_count": 0, "items": [{"id": 1}]}), encoding="utf-8")
        expected.write_text(json.dumps({"status": "pass", "parsed_count": 2, "items": [{"id": 1}, {"id": 2}]}), encoding="utf-8")
        manifest = root / "manifest.json"
        manifest.write_text(json.dumps({"actions": [
            {"type": "read_file", "path": "expected.json"},
            {"type": "write_file", "path": "actual.json", "content": actual.read_text(encoding="utf-8")},
        ]}), encoding="utf-8")
        proc = subprocess.run([
            sys.executable, str(SCRIPT), "--task", "validate domain result", "--manifest-file", str(manifest),
            "--seed-file", f"{expected}=expected.json", "--expected-artifact", "actual.json",
            "--result-status", "actual.json:status", "--result-pass-value", "pass",
            "--compare-json", "actual.json:expected.json", "--require-json-path", "actual.json:parsed_count",
            "--invariant", "actual.json:parsed_count>0", "--out-root", str(root / "runs"), "--run-id", "run-domain-fail",
        ], cwd=ROOT, text=True, capture_output=True, check=False)
        self.assertNotEqual(proc.returncode, 0)
        report = json.loads(proc.stdout)
        self.assertEqual(report["kpis"]["failure_category"], "domain_verdict_failed")
        verdict = json.loads((Path(report["run_dir"]) / "ai_company" / "domain_verdict.json").read_text())
        self.assertEqual(verdict["status"], "fail")
        self.assertGreaterEqual(len(verdict["defects"]), 2)

    def test_iterative_repair_after_missing_expected_artifact(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        root = Path(temp_dir.name)
        responses = [
            json.dumps({"actions": [{"type": "write_file", "path": "notes.txt", "content": "incomplete"}]}),
            json.dumps(
                {
                    "actions": [
                        {"type": "write_file", "path": "final.json", "content": "{\"ok\": true}\n"},
                        {"type": "finish", "summary": "final artifact created", "artifacts": ["final.json"]},
                    ]
                }
            ),
        ]
        with mock.patch.object(executor, "call_ccr_for_manifest", side_effect=responses):
            with mock.patch.object(
                sys,
                "argv",
                [
                    "run_local_model_action_executor.py",
                    "--task",
                    "create final artifact",
                    "--out-root",
                    str(root / "runs"),
                    "--run-id",
                    "run-repair",
                    "--expected-artifact",
                    "final.json",
                    "--max-repair-rounds",
                    "1",
                ],
            ):
                code = executor.main()
        self.assertEqual(code, 0)
        report = json.loads((root / "runs" / "run-repair" / "ai_company" / "task_harness_report.json").read_text())
        self.assertEqual(report["overall_status"], "pass")
        self.assertEqual(report["kpis"]["repair_rounds_used"], 1)
        self.assertEqual(report["kpis"]["missing_expected_artifact_count"], 0)

    def test_seed_file_copies_fixture_into_worktree(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        root = Path(temp_dir.name)
        seed = root / "input_records.json"
        seed.write_text("[{\"value\": 2}, {\"value\": 3}]\n", encoding="utf-8")
        manifest = root / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "actions": [
                        {"type": "read_file", "path": "data/input_records.json"},
                        {"type": "write_file", "path": "output_summary.json", "content": "{\"total\": 5}\n"},
                        {"type": "finish", "summary": "offline fixture analyzed", "artifacts": ["output_summary.json"]},
                    ]
                }
            ),
            encoding="utf-8",
        )
        proc = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--task",
                "analyze seeded fixture",
                "--manifest-file",
                str(manifest),
                "--seed-file",
                f"{seed}=data/input_records.json",
                "--expected-artifact",
                "output_summary.json",
                "--out-root",
                str(root / "runs"),
                "--run-id",
                "run-seed",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        report = json.loads(proc.stdout)
        self.assertEqual(report["overall_status"], "pass")
        self.assertEqual(report["kpis"]["seeded_file_count"], 1)

    def test_json_semantic_expectation_passes(self) -> None:
        proc = self.run_executor(
            {
                "actions": [
                    {"type": "write_file", "path": "output_summary.json", "content": "{\"total_count\": 4, \"average_duration_sec\": 21.25, \"status_counts\": {\"passed\": 2}}\n"},
                    {"type": "finish", "summary": "summary created", "artifacts": ["output_summary.json"]},
                ]
            },
            task="create semantic summary",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        report = json.loads(proc.stdout)
        run_dir = Path(report["run_dir"])

        proc = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--task",
                "create semantic summary",
                "--manifest-file",
                str(run_dir / "results" / "job-001.manifest.raw.txt"),
                "--out-root",
                str(run_dir.parent),
                "--run-id",
                "run-semantic-pass",
                "--expected-artifact",
                "output_summary.json",
                "--expect-json-value",
                "output_summary.json:total_count=4",
                "--expect-json-value",
                "output_summary.json:average_duration_sec=21.25",
                "--expect-json-value",
                "output_summary.json:status_counts.passed=2",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        report = json.loads(proc.stdout)
        self.assertEqual(report["kpis"]["semantic_expectation_count"], 3)
        self.assertEqual(report["kpis"]["semantic_expectation_failed_count"], 0)

    def test_json_semantic_expectation_failure_blocks_success(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        root = Path(temp_dir.name)
        manifest = root / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "actions": [
                        {"type": "write_file", "path": "output_summary.json", "content": "{\"average_duration_sec\": 0.0}\n"},
                        {"type": "finish", "summary": "summary created", "artifacts": ["output_summary.json"]},
                    ]
                }
            ),
            encoding="utf-8",
        )
        proc = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--task",
                "create wrong semantic summary",
                "--manifest-file",
                str(manifest),
                "--expected-artifact",
                "output_summary.json",
                "--expect-json-value",
                "output_summary.json:average_duration_sec=21.25",
                "--out-root",
                str(root / "runs"),
                "--run-id",
                "run-semantic-fail",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(proc.returncode, 0)
        report = json.loads(proc.stdout)
        self.assertEqual(report["overall_status"], "fail")
        self.assertEqual(report["kpis"]["semantic_expectation_failed_count"], 1)
        artifact = json.loads((Path(report["run_dir"]) / "ai_company" / "artifact_verify_report.json").read_text())
        self.assertFalse(artifact["parsed"]["semantic_expectations_passed"])

    def test_iterative_repair_after_semantic_expectation_failure(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        root = Path(temp_dir.name)
        responses = [
            json.dumps(
                {
                    "actions": [
                        {"type": "write_file", "path": "output_summary.json", "content": "{\"average_duration_sec\": 0.0}\n"},
                        {"type": "finish", "summary": "wrong summary", "artifacts": ["output_summary.json"]},
                    ]
                }
            ),
            json.dumps(
                {
                    "actions": [
                        {"type": "write_file", "path": "output_summary.json", "content": "{\"average_duration_sec\": 21.25}\n"},
                        {"type": "finish", "summary": "corrected summary", "artifacts": ["output_summary.json"]},
                    ]
                }
            ),
        ]
        with mock.patch.object(executor, "call_ccr_for_manifest", side_effect=responses):
            with mock.patch.object(
                sys,
                "argv",
                [
                    "run_local_model_action_executor.py",
                    "--task",
                    "repair semantic summary",
                    "--out-root",
                    str(root / "runs"),
                    "--run-id",
                    "run-semantic-repair",
                    "--expected-artifact",
                    "output_summary.json",
                    "--expect-json-value",
                    "output_summary.json:average_duration_sec=21.25",
                    "--max-repair-rounds",
                    "1",
                ],
            ):
                code = executor.main()
        self.assertEqual(code, 0)
        report = json.loads((root / "runs" / "run-semantic-repair" / "ai_company" / "task_harness_report.json").read_text())
        self.assertEqual(report["overall_status"], "pass")
        self.assertEqual(report["kpis"]["repair_rounds_used"], 1)
        self.assertEqual(report["kpis"]["semantic_expectation_failed_count"], 0)

    def test_semantic_repair_feedback_includes_seed_schema_context(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        root = Path(temp_dir.name)
        seed = root / "input_records.json"
        seed.write_text(
            json.dumps(
                [
                    {"id": "a", "status": "passed", "duration_sec": 10.0},
                    {"id": "b", "status": "failed", "duration_sec": 32.5},
                ]
            ),
            encoding="utf-8",
        )
        responses = [
            json.dumps(
                {
                    "actions": [
                        {"type": "write_file", "path": "output_summary.json", "content": "{\"average_duration\": 0.0}\n"},
                        {"type": "finish", "summary": "wrong field used", "artifacts": ["output_summary.json"]},
                    ]
                }
            ),
            json.dumps(
                {
                    "actions": [
                        {"type": "write_file", "path": "output_summary.json", "content": "{\"average_duration\": 21.25}\n"},
                        {"type": "finish", "summary": "schema-aware correction", "artifacts": ["output_summary.json"]},
                    ]
                }
            ),
        ]
        calls: list[tuple[str, int, str]] = []

        def fake_call(task: str, timeout_sec: int, repair_feedback: str = "") -> str:
            calls.append((task, timeout_sec, repair_feedback))
            return responses[len(calls) - 1]

        with mock.patch.object(executor, "call_ccr_for_manifest", side_effect=fake_call):
            with mock.patch.object(
                sys,
                "argv",
                [
                    "run_local_model_action_executor.py",
                    "--task",
                    "calculate average duration from seeded records",
                    "--out-root",
                    str(root / "runs"),
                    "--run-id",
                    "run-schema-aware-repair",
                    "--seed-file",
                    f"{seed}=data/input_records.json",
                    "--expected-artifact",
                    "output_summary.json",
                    "--expect-json-value",
                    "output_summary.json:average_duration=21.25",
                    "--max-repair-rounds",
                    "1",
                ],
            ):
                code = executor.main()
        self.assertEqual(code, 0)
        self.assertGreaterEqual(len(calls), 2)
        self.assertIn("schema_context_for_repair", calls[1][2])
        self.assertIn("duration_sec", calls[1][2])
        report = json.loads((root / "runs" / "run-schema-aware-repair" / "ai_company" / "task_harness_report.json").read_text())
        self.assertTrue(report["kpis"]["schema_context_available"])
        schema_context = json.loads((root / "runs" / "run-schema-aware-repair" / "ai_company" / "schema_context.json").read_text())
        self.assertTrue(schema_context["schema_context_available"])


if __name__ == "__main__":
    unittest.main()
