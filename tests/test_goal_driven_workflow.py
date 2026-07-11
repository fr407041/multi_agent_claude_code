from __future__ import annotations

import json
import tempfile
import unittest
import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

from scripts.goal_driven_workflow import build_final_verdict, deterministic_goal_plan, normalize_goal_plan, validate_goal_plan, verify_job_contract
from scripts.run_goal_driven_workflow import main as run_goal_main
from scripts.materialize_ai_company_task_run import materialize_run
from scripts.run_ai_company_reviewer_worker import verify_summary_artifact
from scripts.validate_ai_company_spec import validate_spec
from scripts.worker_claude_router import build_prompt_details, extract_multi_artifacts

ROOT = Path(__file__).resolve().parents[1]


class GoalDrivenWorkflowTests(unittest.TestCase):
    def test_valid_plan_has_deterministic_topological_order(self) -> None:
        plan = deterministic_goal_plan("Summarize supplied evidence", ["source.txt"])
        report = validate_goal_plan(plan)
        self.assertTrue(report["passed"], report["errors"])
        self.assertEqual(report["topological_order"], ["job-001", "job-002"])

    def test_two_outputs_select_managed_multi_file_contract(self) -> None:
        plan = normalize_goal_plan("Analyze", {"jobs": [{
            "id": "analysis", "capability": "analyze", "inputs": ["source.txt"],
            "outputs": ["analysis.json", "claims.txt"], "tools": ["read", "write"],
            "acceptance_criteria": [
                {"type": "json_valid", "path": "analysis.json"},
                {"type": "artifact_exists", "path": "claims.txt"},
            ],
        }]}, 4, ["source.txt"])
        self.assertEqual(plan["jobs"][0]["worker_template"], "managed_multi_file")
        parsed = extract_multi_artifacts(
            'STATUS: SUCCESS\nARTIFACTS_JSON_START\n{"artifacts":[{"path":"analysis.json","content":"{}"},{"path":"claims.txt","content":"claim"}]}\nARTIFACTS_JSON_END',
            ["analysis.json", "claims.txt"],
        )
        self.assertEqual(set(parsed), {"analysis.json", "claims.txt"})

    def test_live_planner_repairs_invalid_dag_within_budget(self) -> None:
        invalid = '{"jobs":[{"id":"a","capability":"acquire","outputs":["evidence.json"],"tools":["network"],"acceptance_criteria":[{"type":"json_valid","path":"evidence.json"}]}]}'
        valid = '{"jobs":[{"id":"b","capability":"synthesize","inputs":["source.txt"],"outputs":["summary.md"],"tools":["read","write"],"acceptance_criteria":[{"type":"artifact_exists","path":"summary.md"},{"type":"goal_answering"}]}]}'
        responses = [SimpleNamespace(text=invalid, provider_usage={}), SimpleNamespace(text=valid, provider_usage={})]
        with tempfile.TemporaryDirectory() as tmp, patch("scripts.run_goal_driven_workflow.resolve_live_meeting_transport", return_value=("claude_cli", "test")), patch(
            "scripts.run_goal_driven_workflow.call_live_provider_for_turn", side_effect=responses
        ), patch("scripts.run_goal_driven_workflow.subprocess.run", return_value=SimpleNamespace(returncode=0)), patch.object(
            sys, "argv", ["run_goal_driven_workflow.py", "--goal", "Summarize", "--mode", "live", "--out-root", tmp,
                         "--supplied-input", "source.txt", "--max-replans", "1"]
        ):
            self.assertEqual(run_goal_main(), 0)
            artifacts = next((Path(tmp) / ".generated_specs").glob("*-artifacts"))
            self.assertTrue((artifacts / "dag_validation_report.attempt-001.json").is_file())
            self.assertTrue((artifacts / "dag_validation_report.attempt-002.json").is_file())
            self.assertFalse((artifacts / "final_run_verdict.json").exists())

    def test_live_planner_exhaustion_writes_canonical_verdict(self) -> None:
        invalid = '{"jobs":[{"id":"a","capability":"acquire","outputs":["evidence.json"],"tools":["network"],"acceptance_criteria":[{"type":"json_valid","path":"evidence.json"}]}]}'
        responses = [SimpleNamespace(text=invalid, provider_usage={}), SimpleNamespace(text=invalid, provider_usage={})]
        with tempfile.TemporaryDirectory() as tmp, patch("scripts.run_goal_driven_workflow.resolve_live_meeting_transport", return_value=("claude_cli", "test")), patch(
            "scripts.run_goal_driven_workflow.call_live_provider_for_turn", side_effect=responses
        ), patch.object(sys, "argv", ["run_goal_driven_workflow.py", "--goal", "Research", "--mode", "live", "--out-root", tmp, "--max-replans", "1"]):
            self.assertEqual(run_goal_main(), 2)
            artifacts = next((Path(tmp) / ".generated_specs").glob("*-artifacts"))
            verdict = json.loads((artifacts / "final_run_verdict.json").read_text(encoding="utf-8"))
        self.assertEqual(verdict["failure_category"], "PLANNER_CONTRACT_EXHAUSTED")
        self.assertEqual(verdict["planner_attempt_count"], 2)
        self.assertEqual(verdict["model_calls_started"], 2)

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

    def test_verified_subset_is_partial_not_false_pass(self) -> None:
        verdict = build_final_verdict(
            "run-partial", {"passed": True},
            {"jobs": [
                {"job_id": "job-001", "all_passed": True},
                {"job_id": "job-002", "all_passed": False, "failure_category": "ARTIFACT_CONTRACT_FAILED", "failed_checks": []},
            ], "blocked_descendants": ["job-003"]}, [],
        )
        self.assertEqual(verdict["overall_status"], "partial")
        self.assertEqual(verdict["accepted_job_count"], 1)

    def test_empty_json_input_routes_recovery_to_its_producer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "worktree").mkdir()
            (run_dir / "worktree/claims.json").write_text("[]", encoding="utf-8")
            report = verify_job_contract(
                run_dir,
                {"id": "job-002", "inputs": ["claims.json"], "acceptance_criteria": [{"type": "artifact_exists", "path": "summary.md"}]},
                {"status": "SUCCESS", "exit_code": 0}, [],
            )
        self.assertFalse(report["all_passed"])
        self.assertEqual(report["failure_category"], "INPUT_INSUFFICIENT")
        self.assertEqual(report["missing_inputs"], ["claims.json"])

    def test_no_input_goal_materializes_run_owned_worktree(self) -> None:
        spec = json.loads((ROOT / "docs/ai_specs/goal-driven-dependency-recovery-mock.json").read_text(encoding="utf-8"))
        spec["id"] = "goal-no-input-worktree"
        spec["scope_copy_from"] = ""
        spec["goal_plan"]["supplied_inputs"] = []
        with tempfile.TemporaryDirectory() as tmp:
            spec_path = Path(tmp) / "spec.json"
            spec_path.write_text(json.dumps(spec), encoding="utf-8")
            run_dir = materialize_run(spec_path, Path(tmp) / "runs")
            summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
            job = json.loads(next((run_dir / "jobs").glob("job-*.json")).read_text(encoding="utf-8"))
        self.assertEqual(Path(summary["scope_path"]), run_dir / "worktree")
        self.assertEqual(Path(job["scope_path"]), run_dir / "worktree")

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
