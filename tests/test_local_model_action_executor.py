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


if __name__ == "__main__":
    unittest.main()
