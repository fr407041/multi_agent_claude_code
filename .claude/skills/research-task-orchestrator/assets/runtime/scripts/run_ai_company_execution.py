from __future__ import annotations

import json
import os
import subprocess
import sys
import hashlib
import shutil
import time
import urllib.request
from pathlib import Path
from shutil import which

from agent_token_ledger import token_usage_from_text
from agent_profile_resolver import apply_profile_to_job, load_agent_profiles, profile_prompt_note, profile_policy_issues
from bounded_context_loader import build_bounded_context, load_context_defaults
from subagent_claim_ledger import write_claim_ledger
from goal_driven_workflow import descendants, read_json as read_goal_json, recovery_fingerprint, write_json as write_goal_json


ROOT = Path(__file__).resolve().parent.parent
DELIVERABLES_SCRIPTS_DIR = ROOT / "deliverables" / "codex-claude-server-playbook" / "scripts"
CHECKPOINT_JSON_NAME = "main_agent_memory_checkpoint.json"
CHECKPOINT_MD_NAME = "main_agent_memory_checkpoint.md"
CLAIM_LEDGER_NAME = "subagent_claim_ledger.json"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_memory_checkpoint(run_dir: Path) -> dict:
    path = run_dir / "ai_company" / CHECKPOINT_JSON_NAME
    if not path.exists():
        return {}
    try:
        return read_json(path)
    except json.JSONDecodeError:
        return {}


def checkpoint_reassignment_note(run_dir: Path) -> str:
    checkpoint = read_memory_checkpoint(run_dir)
    claim_ledger_path = run_dir / "ai_company" / CLAIM_LEDGER_NAME
    claim_ledger_note = (
        f"Claim ledger file: {claim_ledger_path}\n"
        "Use claim ledger plus the current task as the handoff source. Do not replay full raw logs.\n"
        if claim_ledger_path.exists()
        else ""
    )
    if not checkpoint:
        return claim_ledger_note
    md_path = run_dir / "ai_company" / CHECKPOINT_MD_NAME
    return (
        "\nMAIN_AGENT_MEMORY_CHECKPOINT_AVAILABLE.\n"
        f"Checkpoint file: {md_path}\n"
        f"Checkpoint phase: {checkpoint.get('current_phase', '')}\n"
        f"Next recommended action: {checkpoint.get('next_recommended_action', '')}\n"
        f"{claim_ledger_note}"
        "Use this checkpoint as condensed prior state. Do not request or replay full prior logs.\n"
    )


def resolve_worker_script(script_name: str, local_scripts_dir: Path) -> Path:
    env_dir = os.environ.get("AI_COMPANY_WORKER_SCRIPTS_DIR", "").strip()
    candidates: list[Path] = []
    if env_dir:
        candidates.append(Path(env_dir) / script_name)
    candidates.append(local_scripts_dir / script_name)
    candidates.append(DELIVERABLES_SCRIPTS_DIR / script_name)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return local_scripts_dir / script_name


def ensure_status_file(job: dict, status_path: Path) -> Path:
    if status_path.exists():
        return status_path

    prefix = status_path.with_suffix("")
    raw_path = prefix.with_suffix(".raw.txt")
    log_path = prefix.with_suffix(".exec.log")
    raw = raw_path.read_text(encoding="utf-8", errors="ignore") if raw_path.exists() else ""
    log = log_path.read_text(encoding="utf-8", errors="ignore") if log_path.exists() else ""
    combined = "\n".join([raw, log])
    lowered = combined.lower()

    status = "FAILED"
    note = "worker exited before writing a status file"
    if "CHILD_TIMEOUT" in combined:
        status = "CHILD_TIMEOUT"
        note = "worker timed out before writing status metadata"
    elif "CHILD_LIMIT_REACHED" in combined:
        status = "CHILD_LIMIT_REACHED"
        note = "worker hit the child limit before writing status metadata"
    elif (
        "ROUTER_ERROR" in combined
        or "connection refused" in lowered
        or "connection reset" in lowered
        or "upstream unavailable" in lowered
        or "empty response from router" in lowered
        or "econnreset" in lowered
    ):
        status = "ROUTER_ERROR"
        note = "router transport or upstream failure blocked the child request"
    elif (
        "OVERFLOW_DETECTED" in combined
        or "maximum context length" in lowered
        or "output tokens" in lowered
        or "context window" in lowered
        or "token overflow" in lowered
    ):
        status = "OVERFLOW_DETECTED"
        note = "worker overflowed before writing status metadata"
    elif "NEEDS_REPLAN" in combined:
        status = "NEEDS_REPLAN"
        note = "worker requested replan before writing status metadata"

    payload = {
        "id": job.get("id", status_path.stem.replace(".status", "")),
        "status": status,
        "scope_path": job.get("scope_path", ""),
        "require_change": job.get("require_change", False),
        "files": job.get("files", []),
        "owner_role": job.get("owner_role", ""),
        "agent_profile": job.get("agent_profile", ""),
        "profile_mode": job.get("profile_mode", ""),
        "effective_policy_level": job.get("effective_policy_level", ""),
        "effective_allowed_skills": job.get("effective_allowed_skills", []),
        "effective_allowed_mcp_groups": job.get("effective_allowed_mcp_groups", []),
        "effective_tool_policy": job.get("effective_tool_policy", ""),
        "profile_policy_issues": profile_policy_issues(job),
        "actual_changed_files": [],
        "actual_changed_count": 0,
        "verification_note": note,
        "subagent_summary": note,
        "key_claims": [{"claim": note}],
        "confidence": "low",
        "limitations": [note],
        "handoff_next": "Replan or repair this task before relying on its output.",
        "raw_file": str(raw_path),
        "exec_log_file": str(log_path),
        "success_check": job.get("success_check", ""),
        "test_command": job.get("test_command", ""),
        "test_executed_command": job.get("test_command", ""),
        "test_output_file": str(prefix.with_suffix(".test.txt")),
        "test_exit_code": 0,
        "exit_code": 1 if status == "FAILED" else 0,
        "duration_sec": 0,
        "token_usage": token_usage_from_text(job.get("instruction", ""), combined),
    }
    write_json(status_path, payload)
    return status_path


def choose_worker_script(scripts_dir: Path, job: dict) -> Path:
    worker_template = str(job.get("worker_template", "")).strip().lower()
    if worker_template == "summary_markdown":
        return resolve_worker_script("worker_claude_router_summary_template.sh", scripts_dir)
    if worker_template == "managed_single_file":
        return resolve_worker_script("worker_claude_router_managed_single_file.sh", scripts_dir)
    if worker_template == "managed_multi_file":
        return resolve_worker_script("worker_claude_router_managed_multi_file.sh", scripts_dir)
    if worker_template in {"bounded_worker", "inspection_only"}:
        return resolve_worker_script("worker_claude_router.sh", scripts_dir)
    raise RuntimeError(
        f"SPEC_CONTRACT_INVALID: job {job.get('id', '<unknown>')} has unsupported or missing worker_template"
    )


def local_to_container_path(path: Path) -> str:
    try:
        relative = path.resolve().relative_to(ROOT.resolve())
    except ValueError as exc:
        raise RuntimeError(f"Path is outside workspace root and cannot be mounted into docker worker: {path}") from exc
    return "/workspace/" + relative.as_posix()


def should_use_docker_workers() -> bool:
    if os.environ.get("AI_COMPANY_USE_DOCKER_WORKERS", "").strip() == "1":
        return True
    if os.name == "nt":
        return True
    return False


def run_worker_via_docker(run_dir: Path, worker_script: Path, job_path: Path) -> int:
    docker_bin = os.environ.get("AI_COMPANY_DOCKER_BIN", "docker")
    image = os.environ.get("AI_COMPANY_DOCKER_IMAGE", "claude-ccr:ubuntu22")
    docker_config = Path(os.environ.get("AI_COMPANY_DOCKER_CONFIG", str(ROOT / "tmp" / "docker-config")))
    docker_config.mkdir(parents=True, exist_ok=True)

    local_job = read_json(job_path)
    container_job = dict(local_job)
    container_job["scope_path"] = local_to_container_path(Path(str(local_job.get("scope_path", run_dir / "worktree"))))
    container_job["instruction"] = f"{profile_prompt_note(local_job)}\n\n{local_job.get('instruction', '')}"
    container_job_path = job_path.with_name(f"{job_path.stem}.docker.json")
    write_json(container_job_path, container_job)

    container_script = local_to_container_path(worker_script)
    container_job_file = local_to_container_path(container_job_path)

    env = os.environ.copy()
    env["DOCKER_CONFIG"] = str(docker_config)

    cmd = [
        docker_bin,
        "run",
        "--rm",
        "--add-host=host.docker.internal:host-gateway",
        "-v",
        f"{ROOT}:/workspace",
        "-e",
        f"CCR_PREFERRED_MODEL={env.get('CCR_PREFERRED_MODEL', '')}",
        "-e",
        f"CCR_MAX_OUTPUT_TOKENS={env.get('CCR_MAX_OUTPUT_TOKENS', '1024')}",
        "-e",
        f"CLAUDE_MODEL_ALIAS={env.get('CLAUDE_MODEL_ALIAS', '')}",
        "-e",
        f"CLAUDE_CHILD_TIMEOUT_SEC={env.get('CLAUDE_CHILD_TIMEOUT_SEC', '600')}",
        "-e",
        f"CLAUDE_TOOLS_VALUE={env.get('CLAUDE_TOOLS_VALUE', '')}",
        "-e",
        f"CLAUDE_CHILD_SETTINGS_PATH={local_job.get('profile_settings_overlay') or env.get('CLAUDE_CHILD_SETTINGS_PATH', '')}",
        "-e",
        f"AI_COMPANY_AGENT_PROFILE={local_job.get('agent_profile', '')}",
        "-e",
        f"AI_COMPANY_PROFILE_MODE={local_job.get('profile_mode', '')}",
        "-e",
        "ANTHROPIC_AUTH_TOKEN=local-test-key",
        "-e",
        "ANTHROPIC_BASE_URL=http://127.0.0.1:3456",
        image,
        "bash",
        "-lc",
        f"cd /workspace && bash {container_script} {container_job_file}",
    ]
    proc = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True)
    return proc.returncode


def fallback_owner_role(files: list[str], current_owner: str) -> str:
    lowered = [item.lower() for item in files]
    if any(item.endswith((".md", ".txt", ".rst", ".json")) for item in lowered):
        return "executor_docs" if current_owner != "executor_docs" else "research_agent"
    if any(item.endswith((".py", ".sh", ".js", ".ts")) for item in lowered):
        return "executor_backend" if current_owner != "executor_backend" else "research_agent"
    return "research_agent"


def sync_assignments_to_jobs(run_dir: Path, assignments: list[dict]) -> dict[str, Path]:
    jobs_dir = run_dir / "jobs"
    job_map: dict[str, Path] = {}
    for path in sorted(jobs_dir.glob("job-*.json")):
        job_map[path.stem] = path
        payload = read_json(path)
        job_map[payload.get("id", path.stem)] = path

    for item in assignments:
        if item["owner_role"] == "reviewer_worker":
            continue
        job_path = job_map.get(item["task_id"])
        if not job_path:
            continue
        payload = read_json(job_path)
        payload["owner_role"] = item["owner_role"]
        payload = apply_profile_to_job(payload, str(item.get("profile_mode") or item.get("run_profile_mode") or "auto"), load_agent_profiles())
        if item.get("agent_profile"):
            payload = apply_profile_to_job({**payload, "agent_profile": item.get("agent_profile")}, str(item.get("profile_mode", "auto")), load_agent_profiles())
        payload["depends_on"] = item.get("depends_on", [])
        payload["acceptance_criteria"] = item.get("acceptance_criteria", [])
        payload["fallback_plan"] = item.get("fallback_plan", "")
        payload["files"] = item.get("scope", payload.get("files", []))
        write_json(job_path, payload)
    return job_map


def write_mock_status(run_dir: Path, job: dict, status: str, verification_note: str, changed_count: int = 0) -> Path:
    results_dir = run_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    prefix = results_dir / job["id"]
    raw_file = prefix.with_suffix(".raw.txt")
    test_file = prefix.with_suffix(".test.txt")
    status_file = prefix.with_suffix(".status.json")
    release_summary = "\n".join(
        [
            "- Conditional go is supported by evidence from 42 unit tests, 11 passed backend API tests, and 4 of 5 router prompts, but blocker B1 and blocker B2 must close first.",
            "- The main release risks are router partial response and token overflow; memory guard, claim ledger, reviewer checks, and watchdog recovery reduce but do not remove uncertainty.",
            "- Rollback is documented and tested in staging with a 15 minutes target, so release can proceed only with evidence tracking and rollback monitoring active.",
            "Takeaway: conditional go with rollback, evidence, router, overflow, blocker, and uncertainty gates still enforced.",
            "",
        ]
    )
    job_outputs = {
        "research_evidence.md": "\n".join(
            [
                "- Fixture evidence shows 42 unit tests passed and 11 backend API checks passed; dashboard source is bundled but this mock worker does not start the web runtime.",
                "- Router evidence is bounded: 4 of 5 prompts succeeded, with one partial response retried successfully.",
                "- Release evidence remains conditional because blocker B1 and blocker B2 are still open, and dashboard runtime is tracked separately.",
                "Takeaway: evidence supports conditional go, not clean go.",
                "",
            ]
        ),
        "risk_review.md": "\n".join(
            [
                "- Token overflow is mitigated by memory guard checkpoints that avoid replaying full raw logs.",
                "- Router partial response is mitigated by watchdog classification, bounded retry, and replan instead of infinite loop.",
                "- False success remains a risk, so accepted claims require evidence refs and reviewer verification.",
                "Takeaway: risk is bounded enough for conditional go only after B1 and B2 close.",
                "",
            ]
        ),
        "release_decision.md": "\n".join(
            [
                "- Decision is conditional go, not clean go, because blocker B1 and blocker B2 remain unresolved.",
                "- Evidence includes 42 unit tests, 11 backend API tests, 4 of 5 router prompts, and rollback in 15 minutes.",
                "- Uncertainty remains around router behavior, overflow pressure, and missing 8-hour soak evidence.",
                "Takeaway: proceed only with rollback, evidence, router, overflow, blocker, and uncertainty gates.",
                "",
            ]
        ),
        "summary.md": release_summary,
    }
    raw_file.write_text(
        f"STATUS: {status}\nFILES: {', '.join(job.get('files', []))}\nSUMMARY: mock execution\n",
        encoding="utf-8",
    )
    test_file.write_text("mock test output\n", encoding="utf-8")
    actual_changed_files = job.get("files", [])[:changed_count]
    if status == "SUCCESS" and str(job.get("worker_template", "")).strip().lower() == "summary_markdown":
        scope_path = Path(str(job.get("scope_path", run_dir / "worktree")))
        scope_path.mkdir(parents=True, exist_ok=True)
        written_files = []
        for rel_file in job.get("files", []):
            target = scope_path / rel_file
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(job_outputs.get(rel_file, release_summary), encoding="utf-8")
            written_files.append(rel_file)
        # Keep summary.md valid during intermediate reviewer passes.
        summary_path = scope_path / "summary.md"
        summary_path.write_text(release_summary, encoding="utf-8")
        if "summary.md" not in written_files:
            written_files.append("summary.md")
        actual_changed_files = written_files
    elif status == "SUCCESS" and job.get("capability"):
        scope_path = Path(str(job.get("scope_path", run_dir / "worktree")))
        scope_path.mkdir(parents=True, exist_ok=True)
        written_files = []
        for rel_file in job.get("outputs", job.get("files", [])):
            target = scope_path / rel_file
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.suffix.lower() == ".json":
                target.write_text(json.dumps({"status": "pass", "goal_answered": True, "evidence": job.get("inputs", [])}, indent=2), encoding="utf-8")
            else:
                target.write_text(f"# Goal result\n\nVerified result for the assigned goal using evidence from: {', '.join(job.get('inputs', [])) or 'declared scope'}.\n", encoding="utf-8")
            written_files.append(rel_file)
        actual_changed_files = written_files
    role = str(job.get("agent_profile") or job.get("owner_role") or "default")
    defaults = load_context_defaults()
    context_info = build_bounded_context(
        Path(str(job.get("scope_path", run_dir / "worktree"))),
        [str(item) for item in [*job.get("template_context_files", []), *job.get("files", [])]],
        role=role,
        defaults=defaults,
    )
    evidence_refs = [
        "tests/fixtures/ai_company_release_readiness_demo/release_brief.md",
        "tests/fixtures/ai_company_release_readiness_demo/test_results.md",
        "tests/fixtures/ai_company_release_readiness_demo/risk_log.md",
        "tests/fixtures/ai_company_release_readiness_demo/known_issues.md",
        "tests/fixtures/ai_company_release_readiness_demo/artifact_requirements.json",
    ]
    evidence_refs_scoped = [
        {
            "path": ref,
            "scope": "external_history" if ref.endswith(("release_brief.md", "test_results.md")) else "fixture",
            "note": (
                "Contains historical fixture dashboard smoke text; run the dashboard smoke command for current checkout runtime verification."
                if ref.endswith(("release_brief.md", "test_results.md"))
                else "Fixture input copied into the mock run worktree."
            ),
        }
        for ref in evidence_refs
    ]
    current_checkout_verification = {
        "mock_harness_executed": True,
        "dashboard_runtime_bundled": True,
        "dashboard_runtime_verified_by_mock_harness": False,
        "dashboard_runtime_verified": False,
        "dashboard_runtime_reason": "Dashboard source is bundled; the mock harness does not start web services. Run the dashboard smoke command to verify runtime.",
        "dashboard_smoke_command": "bash agent_os_mvp/smoke-dashboard.sh",
        "dashboard_tracking_issue": None,
    }
    payload = {
        "id": job["id"],
        "status": status,
        "scope_path": job.get("scope_path", ""),
        "require_change": job.get("require_change", False),
        "files": job.get("files", []),
        "owner_role": job.get("owner_role", ""),
        "agent_profile": job.get("agent_profile", ""),
        "profile_mode": job.get("profile_mode", ""),
        "effective_policy_level": job.get("effective_policy_level", ""),
        "effective_allowed_skills": job.get("effective_allowed_skills", []),
        "effective_allowed_mcp_groups": job.get("effective_allowed_mcp_groups", []),
        "effective_tool_policy": job.get("effective_tool_policy", ""),
        "profile_policy_issues": profile_policy_issues(job),
        "actual_changed_files": actual_changed_files,
        "actual_changed_count": len(actual_changed_files),
        "verification_note": verification_note,
        "subagent_summary": "Mock worker produced a bounded release-readiness result with traceable evidence refs.",
        "key_claims": [
            {
                "claim": verification_note if status != "SUCCESS" else "Conditional go is supported only with blocker, rollback, router, overflow, evidence, and uncertainty gates.",
                "evidence_refs": evidence_refs if status == "SUCCESS" else [str(raw_file)],
                "evidence_refs_scoped": evidence_refs_scoped if status == "SUCCESS" else [
                    {"path": str(raw_file), "scope": "generated_current_run", "note": "Failure status raw output from this run."}
                ],
            }
        ],
        "evidence_refs_scoped": evidence_refs_scoped if status == "SUCCESS" else [
            {"path": str(raw_file), "scope": "generated_current_run", "note": "Failure status raw output from this run."}
        ],
        "current_checkout_verification": current_checkout_verification,
        "confidence": "medium" if status == "SUCCESS" else "low",
        "limitations": ["Dashboard source is bundled; this mock worker does not start web services. Run dashboard smoke for runtime verification."] if status == "SUCCESS" else [verification_note],
        "handoff_next": "Use the claim ledger entry and changed artifact for review.",
        "raw_file": str(raw_file),
        "exec_log_file": str(raw_file),
        "success_check": job.get("success_check", ""),
        "test_command": job.get("test_command", ""),
        "test_executed_command": job.get("test_command", ""),
        "test_output_file": str(test_file),
        "test_exit_code": 0,
        "exit_code": 0,
        "duration_sec": 1,
        "context_guard": {
            "input_budget_tokens": context_info.get("budget", {}).get("input_tokens", 0),
            "estimated_input_tokens": context_info.get("estimated_input_tokens", 0),
            "context_budget_exceeded": context_info.get("context_budget_exceeded", False),
            "manifest": context_info.get("manifest", []),
            "skipped_bytes": context_info.get("skipped_bytes", 0),
            "actions": context_info.get("context_guard_actions", []),
            "blocked_before_model": bool(context_info.get("errors")),
            "block_reasons": context_info.get("errors", []),
        },
        "token_usage": token_usage_from_text(job.get("instruction", ""), raw_file.read_text(encoding="utf-8", errors="ignore")),
    }
    write_json(status_file, payload)
    return status_file


def enrich_status_with_profile(job: dict, status_path: Path) -> Path:
    status = read_json(status_path)
    changed = False
    for key in [
        "agent_profile",
        "profile_mode",
        "effective_policy_level",
        "effective_allowed_skills",
        "effective_allowed_mcp_groups",
        "effective_tool_policy",
        "profile_settings_overlay",
        "profile_role_prompt",
        "profile_contract",
        "profile_scope_limits",
        "profile_recovery_policy",
    ]:
        if key not in status and key in job:
            status[key] = job.get(key)
            changed = True
    issues = profile_policy_issues({**job, **status})
    if status.get("profile_policy_issues") != issues:
        status["profile_policy_issues"] = issues
        changed = True
    if changed:
        write_json(status_path, status)
    return status_path


def execute_job(run_dir: Path, scripts_dir: Path, job_path: Path) -> Path:
    job = read_json(job_path)
    if job.get("agent_profile") and "AGENT_PROFILE_ACTIVE" not in str(job.get("instruction", "")):
        job["instruction"] = f"{profile_prompt_note(job)}\n\n{job.get('instruction', '')}"
        write_json(job_path, job)
    results_dir = run_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    status_path = results_dir / f"{job['id']}.status.json"

    if job.get("capability") == "acquire" and job.get("source_url"):
        output = str((job.get("outputs") or job.get("files") or [""])[0])
        scope_root = Path(str(job.get("scope_path", run_dir / "worktree"))).resolve()
        expected_worktree = (run_dir / "worktree").resolve()
        if (read_goal_json(run_dir / "summary.json").get("workflow") or {}).get("mode") == "goal_driven" and scope_root != expected_worktree:
            raise RuntimeError(f"OUTPUT_SCOPE_VIOLATION: goal job scope {scope_root} is not {expected_worktree}")
        target = (scope_root / output).resolve()
        try:
            target.relative_to(scope_root)
        except ValueError as exc:
            raise RuntimeError(f"OUTPUT_SCOPE_VIOLATION: {output}") from exc
        raw_file = results_dir / f"{job['id']}.raw.txt"
        log_file = results_dir / f"{job['id']}.exec.log"
        started = time.monotonic()
        try:
            with urllib.request.urlopen(str(job["source_url"]), timeout=int(os.environ.get("AI_COMPANY_NETWORK_TIMEOUT_SEC", "30"))) as response:
                body = response.read(int(os.environ.get("AI_COMPANY_NETWORK_MAX_BYTES", "1048576")) + 1)
                if len(body) > int(os.environ.get("AI_COMPANY_NETWORK_MAX_BYTES", "1048576")):
                    raise ValueError("network response exceeded bounded byte limit")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
            raw_file.write_bytes(body)
            log_file.write_text(f"GET {job['source_url']}\nbytes={len(body)}\n", encoding="utf-8")
            status = {
                "id": job["id"], "status": "SUCCESS", "scope_path": job.get("scope_path", ""), "files": [output],
                "owner_role": job.get("owner_role", ""), "agent_profile": job.get("agent_profile", ""),
                "actual_changed_files": [output], "actual_changed_count": 1, "failure_family": "",
                "verification_note": "bounded network acquisition completed", "source_url": job["source_url"],
                "raw_file": str(raw_file), "exec_log_file": str(log_file), "exit_code": 0, "test_exit_code": 0,
                "test_executed_command": "", "duration_sec": round(time.monotonic() - started, 3),
                "key_claims": [{"claim": f"Acquired {len(body)} bytes from the declared source.", "evidence_refs": [str(raw_file), str(target)]}],
                "confidence": "medium", "limitations": ["Evidence is limited to the declared bounded response."],
                "token_usage": token_usage_from_text(job.get("instruction", ""), body.decode("utf-8", errors="ignore")),
            }
        except Exception as exc:  # noqa: BLE001
            raw_file.write_text(str(exc), encoding="utf-8")
            log_file.write_text(str(exc), encoding="utf-8")
            status = {
                "id": job["id"], "status": "FAILED", "scope_path": job.get("scope_path", ""), "files": [output],
                "owner_role": job.get("owner_role", ""), "agent_profile": job.get("agent_profile", ""),
                "actual_changed_files": [], "actual_changed_count": 0, "failure_family": "external_dependency",
                "verification_note": f"external dependency acquisition failed: {exc}", "source_url": job["source_url"],
                "raw_file": str(raw_file), "exec_log_file": str(log_file), "exit_code": 1, "test_exit_code": 1,
                "test_executed_command": "", "duration_sec": round(time.monotonic() - started, 3), "key_claims": [],
                "confidence": "low", "limitations": [str(exc)], "token_usage": token_usage_from_text(job.get("instruction", ""), str(exc)),
            }
        write_json(status_path, status)
        return enrich_status_with_profile(job, status_path)

    if os.environ.get("AI_COMPANY_EXECUTION_MOCK", "0") == "1":
        sequence = list(job.get("mock_status_sequence", []))
        mock_status = sequence.pop(0) if sequence else job.get("mock_status", "SUCCESS")
        if job.get("mock_status_sequence") is not None:
            job["mock_status_sequence"] = sequence
            write_json(job_path, job)
        note_map = {
            "SUCCESS": "mock success",
            "NEEDS_REPLAN": "worker requested replan in mock mode",
            "FAILED": "mock failure",
            "OVERFLOW_DETECTED": "mock overflow",
        }
        changed_count = 1 if mock_status == "SUCCESS" and job.get("require_change") else 0
        return write_mock_status(run_dir, job, mock_status, note_map.get(mock_status, "mock status"), changed_count)

    worker_script = choose_worker_script(scripts_dir, job)
    if should_use_docker_workers():
        run_worker_via_docker(run_dir, worker_script, job_path)
    else:
        bash_bin = which("bash") or "bash"
        subprocess.run([bash_bin, str(worker_script), str(job_path)], cwd=run_dir, check=False)
    return enrich_status_with_profile(job, ensure_status_file(job, status_path))


def run_reviewer(run_dir: Path, scripts_dir: Path) -> dict:
    reviewer_script = scripts_dir / "run_ai_company_reviewer_worker.py"
    reviewer_out = run_dir / "ai_company" / "reviewer_verdicts.json"
    subprocess.run([sys.executable, str(reviewer_script), str(run_dir), str(reviewer_out)], check=True)
    return read_json(reviewer_out)


def reusable_terminal_status(run_dir: Path, task_id: str) -> dict | None:
    status_path = run_dir / "results" / f"{task_id}.status.json"
    if not status_path.is_file():
        return None
    try:
        status = read_json(status_path)
    except json.JSONDecodeError:
        return None
    if status.get("status") not in {
        "SUCCESS",
        "FAILED",
        "NEEDS_REPLAN",
        "OVERFLOW_DETECTED",
        "ROUTER_ERROR",
        "CHILD_TIMEOUT",
        "CHILD_LIMIT_REACHED",
        "PSEUDO_TOOL_CALL_DETECTED",
    }:
        return None
    return status


def create_reassignment_job(run_dir: Path, source_job: dict, verdict: dict, attempt: int) -> Path:
    jobs_dir = run_dir / "jobs"
    new_id = f"{source_job['id']}-reassign-{attempt:02d}"
    narrowed_files = list(source_job.get("files", []))[:1] or list(source_job.get("files", []))
    owner_role = fallback_owner_role(narrowed_files, source_job.get("owner_role", "research_agent"))
    payload = dict(source_job)
    payload["id"] = new_id
    payload["title"] = f"{source_job.get('title', source_job['id'])} reassigned {attempt}"
    payload["instruction"] = (
        "REASSIGNMENT after reviewer verdict.\n"
        f"Previous verdict: {verdict['verdict']}.\n"
        "Work on the smallest safe scope only and satisfy the original acceptance criteria.\n\n"
        f"{checkpoint_reassignment_note(run_dir)}\n"
        f"Original instruction:\n{source_job.get('instruction', '')}"
    )
    payload["files"] = narrowed_files
    payload["owner_role"] = owner_role
    if (
        payload.get("require_change")
        and len(narrowed_files) == 1
        and not str(payload.get("worker_template", "")).strip()
    ):
        payload["worker_template"] = "managed_single_file"
    payload = apply_profile_to_job(payload, str(source_job.get("profile_mode", "auto")), load_agent_profiles())
    payload["force_worker_mode"] = "managed_single_file" if len(narrowed_files) == 1 and payload.get("require_change", False) else "auto"
    if os.environ.get("AI_COMPANY_EXECUTION_MOCK", "0") == "1":
        payload["mock_status"] = "SUCCESS"
    new_path = jobs_dir / f"{new_id}.json"
    write_json(new_path, payload)
    return new_path


def build_summary(run_dir: Path, execution_log: list[dict], reviewer: dict, reassignment_count: int) -> dict:
    accepted = reviewer.get("accepted_count", 0)
    total = reviewer.get("verdict_count", 0)
    return {
        "run_id": run_dir.name,
        "execution_jobs_run": len(execution_log),
        "reassignment_count": reassignment_count,
        "accepted_count": accepted,
        "reviewer_verdict_count": total,
        "acceptance_rate": round(accepted / total, 3) if total else 0.0,
        "execution_log": execution_log,
    }


def _verdict_for(reviewer: dict, task_id: str) -> dict:
    return next((item for item in reviewer.get("verdicts", []) if item.get("task_id") == task_id), {})


def _archive_attempt(run_dir: Path, task_id: str, attempt: int) -> None:
    destination = run_dir / "results" / "attempts" / task_id / f"attempt-{attempt:02d}"
    destination.mkdir(parents=True, exist_ok=True)
    for path in (run_dir / "results").glob(f"{task_id}.*"):
        if path.is_file():
            shutil.copy2(path, destination / path.name)


def _write_blocked_status(run_dir: Path, job: dict, failed_dependencies: list[str]) -> None:
    status_path = run_dir / "results" / f"{job['id']}.status.json"
    payload = {
        "id": job["id"], "status": "BLOCKED_BY_DEPENDENCY", "scope_path": job.get("scope_path", ""),
        "files": job.get("files", []), "owner_role": job.get("owner_role", ""), "agent_profile": job.get("agent_profile", ""),
        "profile_mode": job.get("profile_mode", ""), "effective_policy_level": job.get("effective_policy_level", ""),
        "effective_tool_policy": job.get("effective_tool_policy", ""),
        "actual_changed_files": [], "actual_changed_count": 0, "failure_family": "dependency",
        "verification_note": f"Blocked until dependencies pass: {', '.join(failed_dependencies)}",
        "failed_dependencies": failed_dependencies, "key_claims": [], "confidence": "low", "limitations": ["Dependency contract did not pass."],
        "raw_file": "", "exec_log_file": "", "exit_code": 1,
        "token_usage": token_usage_from_text(job.get("instruction", ""), ""),
    }
    write_json(status_path, payload)


def execute_goal_driven(run_dir: Path, scripts_dir: Path, job_map: dict[str, Path]) -> tuple[list[dict], dict, int]:
    goal_plan = read_json(run_dir / "ai_company" / "goal_plan.json")
    jobs = goal_plan.get("jobs", [])
    order = read_json(run_dir / "ai_company" / "dag_validation_report.json").get("topological_order", [])
    jobs_by_id = {str(job["id"]): read_json(job_map[str(job["id"])]) for job in jobs}
    accepted: set[str] = set()
    states: dict[str, dict] = {}
    execution_log: list[dict] = []
    recovery_events: list[dict] = []
    seen_fingerprints: set[str] = set()
    max_total = int(os.environ.get("AI_COMPANY_MAX_REASSIGNMENTS_PER_RUN", "2"))
    summary = read_json(run_dir / "summary.json")
    max_per_job = int((summary.get("recovery") or {}).get("max_attempts_per_job", 2))
    recoveries = 0

    for task_id in order:
        job = jobs_by_id[task_id]
        failed_dependencies = [dep for dep in job.get("depends_on", []) if dep not in accepted]
        if failed_dependencies:
            _write_blocked_status(run_dir, job, failed_dependencies)
            states[task_id] = {"state": "blocked", "failed_dependencies": failed_dependencies}
            execution_log.append({"task_id": task_id, "owner_role": job.get("owner_role"), "mode": "blocked", "status": "BLOCKED_BY_DEPENDENCY"})
            continue

        execute_job(run_dir, scripts_dir, job_map[task_id])
        write_claim_ledger(run_dir)
        reviewer = run_reviewer(run_dir, scripts_dir)
        verdict = _verdict_for(reviewer, task_id)
        execution_log.append({"task_id": task_id, "owner_role": job.get("owner_role"), "mode": "initial", "status": verdict.get("verdict")})
        if verdict.get("verdict") == "ACCEPTED":
            accepted.add(task_id)
            states[task_id] = {"state": "accepted", "attempts": 1}
            continue

        recovery_id = str(verdict.get("recommended_recovery_job") or task_id)
        recovery_job = jobs_by_id.get(recovery_id, job)
        base_instruction = str(recovery_job.get("instruction", ""))
        initial_fingerprint = recovery_fingerprint(
            recovery_job, verdict.get("missing_inputs", []), verdict.get("failed_checks", []),
            base_instruction, run_dir / "worktree",
        )
        seen_fingerprints.add(initial_fingerprint)
        recovered = False
        for attempt in range(1, max_per_job + 1):
            if recoveries >= max_total:
                recovery_events.append({"job_id": recovery_id, "action": "RECOVERY_BUDGET_EXHAUSTED", "attempt": attempt})
                break
            failed_checks = verdict.get("failed_checks", [])
            missing_inputs = verdict.get("missing_inputs", [])
            recovery_core = (
                f"DEPENDENCY_AWARE_RECOVERY. Repair only job {recovery_id}. "
                f"Missing inputs: {missing_inputs}. Failed checks: {json.dumps(failed_checks, ensure_ascii=False)}. "
                "Paths in artifact_exists failures are outputs to create, not prerequisite inputs. "
                "Do not replay full logs; satisfy the declared typed criteria and preserve evidence refs."
            )
            candidate_instruction = f"{recovery_core}\n\nOriginal instruction:\n{base_instruction}"
            fingerprint = recovery_fingerprint(recovery_job, missing_inputs, failed_checks, candidate_instruction, run_dir / "worktree")
            if fingerprint in seen_fingerprints:
                recovery_events.append({"job_id": recovery_id, "action": "UNCHANGED_RETRY_BLOCKED", "attempt": attempt, "fingerprint": fingerprint})
                break
            seen_fingerprints.add(fingerprint)
            recoveries += 1
            _archive_attempt(run_dir, recovery_id, attempt)
            recovery_path = job_map[recovery_id]
            recovery_payload = read_json(recovery_path)
            recovery_payload["instruction"] = candidate_instruction
            recovery_payload["recovery_fingerprint"] = fingerprint
            write_json(recovery_path, recovery_payload)
            status_path = run_dir / "results" / f"{recovery_id}.status.json"
            if status_path.exists():
                status_path.unlink()
            execute_job(run_dir, scripts_dir, recovery_path)
            write_claim_ledger(run_dir)
            reviewer = run_reviewer(run_dir, scripts_dir)
            verdict = _verdict_for(reviewer, recovery_id)
            affected = descendants(jobs, recovery_id)
            recovery_events.append({
                "job_id": recovery_id, "action": "reexecuted_dependency", "attempt": attempt,
                "fingerprint": fingerprint, "verdict": verdict.get("verdict"), "invalidated_descendants": affected,
            })
            execution_log.append({"task_id": recovery_id, "owner_role": recovery_job.get("owner_role"), "mode": "dependency_recovery", "status": verdict.get("verdict")})
            if verdict.get("verdict") == "ACCEPTED":
                accepted.add(recovery_id)
                states[recovery_id] = {"state": "accepted", "attempts": attempt + 1}
                for descendant in affected:
                    descendant_status = run_dir / "results" / f"{descendant}.status.json"
                    if descendant_status.exists() and read_json(descendant_status).get("status") == "BLOCKED_BY_DEPENDENCY":
                        descendant_status.unlink()
                recovered = True
                break
        if not recovered:
            states[task_id] = {"state": "failed", "verdict": verdict.get("verdict"), "attempts": 1}

    # A repaired producer may unblock descendants that were encountered earlier.
    for task_id in order:
        if states.get(task_id, {}).get("state") != "blocked":
            continue
        job = jobs_by_id[task_id]
        failed_dependencies = [dep for dep in job.get("depends_on", []) if dep not in accepted]
        if failed_dependencies:
            states[task_id] = {"state": "blocked", "failed_dependencies": failed_dependencies}
            continue
        status_path = run_dir / "results" / f"{task_id}.status.json"
        if status_path.exists():
            status_path.unlink()
        execute_job(run_dir, scripts_dir, job_map[task_id])
        write_claim_ledger(run_dir)
        reviewer = run_reviewer(run_dir, scripts_dir)
        verdict = _verdict_for(reviewer, task_id)
        execution_log.append({"task_id": task_id, "owner_role": job.get("owner_role"), "mode": "unblocked_descendant", "status": verdict.get("verdict")})
        if verdict.get("verdict") == "ACCEPTED":
            accepted.add(task_id)
            states[task_id] = {"state": "accepted", "attempts": 1}
        else:
            states[task_id] = {"state": "failed", "verdict": verdict.get("verdict"), "attempts": 1}

    reviewer = run_reviewer(run_dir, scripts_dir)
    blocked = [job_id for job_id in order if job_id not in accepted and states.get(job_id, {}).get("state") == "blocked"]
    dependency_state = {"run_id": run_dir.name, "states": states, "accepted": sorted(accepted), "blocked_descendants": blocked}
    write_goal_json(run_dir / "ai_company" / "dependency_state.json", dependency_state)
    trace_path = run_dir / "ai_company" / "recovery_trace.jsonl"
    trace_path.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in recovery_events), encoding="utf-8")
    return execution_log, reviewer, recoveries


def main() -> None:
    if len(sys.argv) not in {2, 3}:
        raise SystemExit("Usage: run_ai_company_execution.py <run_dir> [out_file]")
    run_dir = Path(sys.argv[1]).resolve()
    out_file = Path(sys.argv[2]).resolve() if len(sys.argv) == 3 else run_dir / "ai_company" / "execution_summary.json"
    scripts_dir = Path(__file__).resolve().parent
    meeting_path = run_dir / "ai_company" / "meeting_decision.json"
    meeting = read_json(meeting_path)
    assignments = meeting.get("task_assignments", [])
    job_map = sync_assignments_to_jobs(run_dir, assignments)

    run_summary = read_json(run_dir / "summary.json")
    if (run_summary.get("workflow") or {}).get("mode") == "goal_driven":
        execution_log, reviewer, recovery_count = execute_goal_driven(run_dir, scripts_dir, job_map)
        summary = build_summary(run_dir, execution_log, reviewer, recovery_count)
        summary["workflow_mode"] = "goal_driven"
        summary["dependency_state_file"] = "ai_company/dependency_state.json"
        summary["recovery_trace_file"] = "ai_company/recovery_trace.jsonl"
        write_json(out_file, summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return

    execution_log: list[dict] = []
    non_review_tasks = [item for item in assignments if item.get("owner_role") != "reviewer_worker"]
    for item in non_review_tasks:
        job_path = job_map.get(item["task_id"])
        if not job_path:
            continue
        existing_status = reusable_terminal_status(run_dir, item["task_id"])
        if existing_status is not None:
            execution_log.append(
                {
                    "task_id": item["task_id"],
                    "owner_role": item["owner_role"],
                    "mode": "resume_existing",
                    "status": existing_status.get("status"),
                }
            )
            continue
        execute_job(run_dir, scripts_dir, job_path)
        write_claim_ledger(run_dir)
        execution_log.append({"task_id": item["task_id"], "owner_role": item["owner_role"], "mode": "initial"})

    write_claim_ledger(run_dir)
    reviewer = run_reviewer(run_dir, scripts_dir)
    reassignment_count = 0
    max_reassign = int(os.environ.get("AI_COMPANY_MAX_REASSIGNMENTS_PER_RUN", "1"))
    attempt = 1
    while attempt <= max_reassign:
        actionable = [
            verdict for verdict in reviewer.get("verdicts", [])
            if verdict.get("verdict") in {"REPLAN_REQUIRED", "REPAIR_REQUIRED", "FALSE_SUCCESS_BLOCKED"} and not verdict["task_id"].endswith("-review")
        ]
        if not actionable:
            break
        for verdict in actionable:
            if reassignment_count >= max_reassign:
                break
            source_path = job_map.get(verdict["task_id"])
            if not source_path:
                continue
            source_job = read_json(source_path)
            reassigned_job_path = create_reassignment_job(run_dir, source_job, verdict, attempt)
            job_map[reassigned_job_path.stem] = reassigned_job_path
            job_map[read_json(reassigned_job_path)["id"]] = reassigned_job_path
            reassigned_job = read_json(reassigned_job_path)
            existing_reassignment = reusable_terminal_status(run_dir, reassigned_job["id"])
            if existing_reassignment is None:
                execute_job(run_dir, scripts_dir, reassigned_job_path)
            write_claim_ledger(run_dir)
            reassignment_count += 1
            execution_log.append(
                {
                    "task_id": reassigned_job["id"],
                    "owner_role": reassigned_job["owner_role"],
                    "mode": "resume_existing_reassignment" if existing_reassignment is not None else "reassignment",
                    "source_task_id": verdict["task_id"],
                }
            )
        write_claim_ledger(run_dir)
        reviewer = run_reviewer(run_dir, scripts_dir)
        attempt += 1

    summary = build_summary(run_dir, execution_log, reviewer, reassignment_count)
    write_json(out_file, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
