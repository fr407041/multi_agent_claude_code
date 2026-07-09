from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
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


if __name__ == "__main__":
    unittest.main()
