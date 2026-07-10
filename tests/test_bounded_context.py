from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from bounded_context_loader import build_bounded_context, load_file_context  # noqa: E402


class BoundedContextTests(unittest.TestCase):
    def test_large_file_is_chunked_without_full_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "large.md"
            path.write_text("A" * 2000, encoding="utf-8")
            result = load_file_context(
                path,
                max_context_tokens=100,
                soft_limit_bytes=100,
                hard_limit_bytes=5000,
                chunk_chars=100,
                overlap_chars=10,
                max_chunks=4,
            )
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["context_guard_action"], "chunked_context")
            self.assertLess(result["included_chars"], 2000)
            self.assertLessEqual(result["included_chars"], 400)
            self.assertGreater(result["skipped_bytes"], 0)
            self.assertGreaterEqual(len(result["chunks"]), 2)

    def test_hard_file_limit_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "too-large.log"
            path.write_text("x" * 1000, encoding="utf-8")
            result = load_file_context(
                path,
                max_context_tokens=100,
                soft_limit_bytes=100,
                hard_limit_bytes=100,
                chunk_chars=100,
                overlap_chars=0,
                max_chunks=4,
            )
            self.assertEqual(result["status"], "INPUT_FILE_TOO_LARGE")
            self.assertEqual(result["chunks"], [])
            self.assertEqual(result["skipped_bytes"], 1000)

    def test_worker_blocks_before_provider_for_oversized_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            jobs = run_dir / "jobs"
            worktree = run_dir / "worktree"
            jobs.mkdir(parents=True)
            worktree.mkdir(parents=True)
            (worktree / "input.md").write_text("x" * 1000, encoding="utf-8")
            defaults = root / "defaults.json"
            defaults.write_text(
                json.dumps(
                    {
                        "file_soft_limit_bytes": 100,
                        "file_hard_limit_bytes": 100,
                        "context_hard_tokens_per_turn": 200,
                        "role_context_budgets": {"default": {"input_tokens": 100, "output_tokens": 100}},
                    }
                ),
                encoding="utf-8",
            )
            job = {
                "id": "job-large",
                "owner_role": "research_agent",
                "files": ["input.md"],
                "scope_path": str(worktree),
                "instruction": "Inspect the input.",
                "worker_template": "bounded_worker",
            }
            job_path = jobs / "job-large.json"
            job_path.write_text(json.dumps(job), encoding="utf-8")
            env = os.environ.copy()
            env["AI_COMPANY_DEFAULTS_FILE"] = str(defaults)
            env["AI_COMPANY_LIVE_WORKER_TRANSPORT"] = "claude_cli"
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "worker_claude_router.py"), str(job_path), "bounded"],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                timeout=20,
            )
            self.assertNotEqual(result.returncode, 0)
            status = json.loads((run_dir / "results" / "job-large.status.json").read_text(encoding="utf-8"))
            self.assertEqual(status["status"], "OVERFLOW_DETECTED")
            self.assertEqual(status["transport"], "context_guard")
            self.assertTrue(status["context_guard"]["blocked_before_model"])


if __name__ == "__main__":
    unittest.main()
