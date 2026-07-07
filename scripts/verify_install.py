#!/usr/bin/env python3
"""Verify that a fresh checkout contains the runnable mock demo assets."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_PATHS = [
    ".claude/skills/research-task-orchestrator/SKILL.md",
    ".claude/skills/research-task-orchestrator/scripts/install_dashboard.sh",
    ".claude/skills/research-task-orchestrator/scripts/start_dashboard.sh",
    "scripts/run_ai_company_task_harness.py",
    "docs/ai_specs/ai-company-release-readiness-strict-demo.json",
    "tests/fixtures/ai_company_release_readiness_demo/release_brief.md",
    "tests/fixtures/ai_company_release_readiness_demo/test_results.md",
    "tests/fixtures/ai_company_release_readiness_demo/risk_log.md",
    "tests/fixtures/ai_company_release_readiness_demo/known_issues.md",
    "tests/fixtures/ai_company_release_readiness_demo/artifact_requirements.json",
    "agent_os_mvp/README.md",
]


def main() -> int:
    missing = []
    for rel_path in REQUIRED_PATHS:
        if not (ROOT / rel_path).exists():
            missing.append(rel_path)

    spec_path = ROOT / "docs/ai_specs/ai-company-release-readiness-strict-demo.json"
    spec_valid = False
    spec_error = None
    if spec_path.exists():
        try:
            json.loads(spec_path.read_text(encoding="utf-8"))
            spec_valid = True
        except Exception as exc:
            spec_error = str(exc)

    print("multi_agent_claude_code install verification")
    print(f"root: {ROOT}")
    print(f"required paths: {len(REQUIRED_PATHS)}")
    print(f"present: {len(REQUIRED_PATHS) - len(missing)}")
    print(f"missing: {len(missing)}")
    print(f"demo spec json valid: {spec_valid}")

    if missing:
        print("\nMISSING:")
        for rel_path in missing:
            print(f"- {rel_path}")

    if spec_error:
        print(f"\nSPEC ERROR: {spec_error}")

    if missing or not spec_valid:
        print("\nInstall verification failed. This checkout is not runnable yet.")
        return 1

    print("\nInstall verification passed.")
    print("Next command:")
    print("python3 scripts/run_ai_company_task_harness.py docs/ai_specs/ai-company-release-readiness-strict-demo.json --mode mock")
    return 0


if __name__ == "__main__":
    sys.exit(main())
