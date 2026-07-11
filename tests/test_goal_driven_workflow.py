from __future__ import annotations

import json
import tempfile
import unittest
import os
import subprocess
import sys
from pathlib import Path

from scripts.goal_driven_workflow import deterministic_goal_plan, validate_goal_plan, verify_job_contract
from scripts.run_ai_company_reviewer_worker import verify_summary_artifact
from scripts.validate_ai_company_spec import validate_spec
from scripts.worker_claude_router import build_prompt_details

ROOT = Path(__file__).resolve().parents[1]


class GoalDrivenWorkflowTests(unittest.TestCase):
    def test_valid_plan_has_deterministic_topological_order(self) -> None:
        plan = deterministic_goal_plan("Summarize supplied evidence", ["source.txt"])
        report = validate_goal_plan(plan)
        self.assertTrue(report["passed"], report["errors"])
        self.assertEqual(report["topological_order"], ["job-001", "job-002"])

    def test_cycle_missing_producer_unsafe_path_and_tool_are_rejected(self) -> None:
        plan = {
            "supplied_inputs": [],
            "jobs": [
                {
                    "id": "job-001", "capability": "analyze", "depends_on": ["job-002"],
                    "inputs": ["missing.json"], "outputs": ["../escape.json"], "tools": ["root_shell"],
                    "acceptance_criteria": [{"type": "free_form"}],
                },
                {
                    "id": "job-002", "capability": "synthesize", "depends_on": ["job-001"],
                    "inputs": [], "outputs": ["summary.md"], "tools": ["write"],
                    "acceptance_criteria": [{"type": "artifact_exists", "path": "summary.md"}],
                },
            ],
        }
        codes = {item["code"] for item in validate_goal_plan(plan)["errors"]}
        self.assertTrue({"DAG_CYCLE", "DAG_INPUT_PRODUCER_MISSING", "DAG_PATH_UNSAFE", "DAG_TOOL_UNSUPPORTED", "DAG_CRITERION_UNSUPPORTED"}.issubset(codes))

    def test_generic_contract_blocks_missing_input_before_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "worktree").mkdir()
            job = {
                "id": "job-001", "inputs": ["evidence.json"], "outputs": ["analysis.json"],
                "acceptance_criteria": [{"type": "json_valid", "path": "analysis.json"}],
            }
            status = {"status": "SUCCESS", "exit_code": 0}
            report = verify_job_contract(run_dir, job, status, [{"claim": "looks good", "evidence_refs": ["raw.txt"]}])
        self.assertFalse(report["all_passed"])
        self.assertEqual(report["failure_category"], "INPUT_INSUFFICIENT")
        self.assertEqual(report["missing_inputs"], ["evidence.json"])

    def test_external_dependency_failure_is_not_reported_as_model_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "worktree").mkdir()
            job = {
                "id": "job-001", "inputs": [], "outputs": ["acquired.json"],
                "acceptance_criteria": [{"type": "json_valid", "path": "acquired.json"}],
            }
            status = {
                "status": "FAILED", "exit_code": 1, "failure_family": "external_dependency",
                "verification_note": "Local fixture server was unavailable.",
            }
            report = verify_job_contract(run_dir, job, status, [])
        self.assertFalse(report["all_passed"])
        self.assertEqual(report["failure_category"], "EXTERNAL_DEPENDENCY_FAILED")
        self.assertEqual(report["missing_artifacts"], ["acquired.json"])

    def test_common_summary_never_implicitly_uses_fixture_verifier(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            scope = Path(tmp)
            (scope / "summary.md").write_text("Generic summary", encoding="utf-8")
            self.assertIsNone(verify_summary_artifact(scope))

    def test_managed_goal_worker_reads_inputs_not_missing_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            scope = Path(tmp)
            (scope / "source.txt").write_text("bounded input", encoding="utf-8")
            job = {
                "id": "job-001", "capability": "analyze", "inputs": ["source.txt"], "files": ["new-output.md"],
                "instruction": "Create the output", "acceptance_criteria": [{"type": "artifact_exists", "path": "new-output.md"}],
            }
            _, context = build_prompt_details(job, "managed", scope)
        self.assertFalse(context.get("errors"), context.get("errors"))
        self.assertEqual(len(context["manifest"]), 1)
        self.assertTrue(context["manifest"][0]["path"].replace("\\", "/").endswith("/source.txt"))

    def test_fixture_verifier_is_rejected_for_goal_driven_spec(self) -> None:
        spec = json.loads((ROOT / "docs/ai_specs/goal-driven-dependency-recovery-mock.json").read_text(encoding="utf-8"))
        spec["verification"] = {"type": "fixture", "verifier_id": "sens_summary"}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad-verifier.json"
            path.write_text(json.dumps(spec), encoding="utf-8")
            report = validate_spec(path, ROOT)
        self.assertFalse(report["passed"])
        self.assertIn("VERIFIER_SCOPE_MISMATCH", {item["code"] for item in report["errors"]})

    def test_dependency_recovery_mock_is_selective_and_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env["PYTHONPATH"] = str(ROOT / "scripts")
            proc = subprocess.run(
                [sys.executable, str(ROOT / "scripts/run_ai_company_task_harness.py"),
                 str(ROOT / "docs/ai_specs/goal-driven-dependency-recovery-mock.json"),
                 "--mode", "mock", "--out-root", tmp],
                cwd=ROOT, env=env, text=True, capture_output=True, check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            report = json.loads(proc.stdout)
            run_dir = Path(report["run_dir"])
            trace = [json.loads(line) for line in (run_dir / "ai_company/recovery_trace.jsonl").read_text(encoding="utf-8").splitlines()]
            execution = json.loads((run_dir / "ai_company/execution_summary.json").read_text(encoding="utf-8"))
        self.assertEqual(report["overall_status"], "pass")
        self.assertEqual(report["kpis"]["reassignment_count"], 1)
        self.assertEqual(trace[0]["job_id"], "job-001")
        self.assertEqual(trace[0]["invalidated_descendants"], ["job-002"])
        self.assertEqual([item["task_id"] for item in execution["execution_log"]], ["job-001", "job-001", "job-002"])

    def test_unchanged_retry_is_blocked_and_recorded(self) -> None:
        spec = json.loads((ROOT / "docs/ai_specs/goal-driven-dependency-recovery-mock.json").read_text(encoding="utf-8"))
        spec["id"] = "goal-driven-unchanged-retry"
        spec["goal_plan"]["jobs"][0]["mock_status_sequence"] = ["FAILED", "FAILED", "SUCCESS"]
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spec_path = tmp_path / "spec.json"
            spec_path.write_text(json.dumps(spec), encoding="utf-8")
            env = os.environ.copy()
            env["PYTHONPATH"] = str(ROOT / "scripts")
            proc = subprocess.run(
                [sys.executable, str(ROOT / "scripts/run_ai_company_task_harness.py"), str(spec_path),
                 "--mode", "mock", "--out-root", str(tmp_path / "runs")],
                cwd=ROOT, env=env, text=True, capture_output=True, check=False,
            )
            report = json.loads(proc.stdout)
            trace = [json.loads(line) for line in (Path(report["run_dir"]) / "ai_company/recovery_trace.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(report["overall_status"], "fail")
        self.assertIn("UNCHANGED_RETRY_BLOCKED", [item["action"] for item in trace])


if __name__ == "__main__":
    unittest.main()
