#!/usr/bin/env python3
"""Verify that the live CCR path can produce an actual file side effect."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    run_root = Path(os.environ.get("LIVE_SIDE_EFFECT_SMOKE_ROOT", tempfile.mkdtemp(prefix="ai-company-live-side-effect-"))).resolve()
    if run_root.exists():
        shutil.rmtree(run_root)
    scope = run_root / "worktree"
    jobs = run_root / "jobs"
    target_rel = "open_source_probe_side_effect.txt"
    expected = "local-model-side-effect-ok\n"
    scope.mkdir(parents=True, exist_ok=True)
    jobs.mkdir(parents=True, exist_ok=True)
    job = {
        "id": "job-side-effect-probe",
        "owner_role": "executor_backend",
        "agent_profile": "executor_backend",
        "profile_mode": "strict",
        "effective_policy_level": "enforced-by-wrapper + verified-posthoc",
        "effective_tool_policy": "limited",
        "scope_path": str(scope),
        "files": [target_rel],
        "require_change": True,
        "worker_template": "managed_single_file",
        "instruction": (
            f"Create or overwrite {target_rel} with exactly this content, including the trailing newline:\n"
            f"{expected}\n"
            "Return final file content only. Do not describe a tool call."
        ),
        "test_command": (
            "python -c \"from pathlib import Path; "
            f"p=Path('{target_rel}'); "
            f"expected={expected!r}; "
            "raise SystemExit(0 if p.exists() and p.read_text(encoding='utf-8') == expected else 1)\""
        ),
    }
    job_path = jobs / "job-side-effect-probe.json"
    write_json(job_path, job)
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "worker_claude_router.py"), str(job_path), "managed"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    status_path = run_root / "results" / "job-side-effect-probe.status.json"
    target = scope / target_rel
    status = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {}
    ok = proc.returncode == 0 and status.get("status") == "SUCCESS" and target.exists() and target.read_text(encoding="utf-8") == expected
    report = {
        "side_effect_smoke": "pass" if ok else "fail",
        "run_root": str(run_root),
        "target_file": str(target),
        "target_exists": target.exists(),
        "target_content_matches": target.exists() and target.read_text(encoding="utf-8") == expected,
        "worker_exit_code": proc.returncode,
        "worker_status": status.get("status", "MISSING_STATUS"),
        "pseudo_tool_call_detected": bool(status.get("pseudo_tool_call_detected")),
        "failure_family": status.get("failure_family", ""),
        "stdout_excerpt": proc.stdout[:1200],
        "stderr_excerpt": proc.stderr[:1200],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
