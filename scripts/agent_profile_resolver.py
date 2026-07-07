#!/usr/bin/env python3
"""Compatibility profile resolver entry for the published mock package."""

from __future__ import annotations

import json
import sys


def main() -> int:
    payload = {
        "entrypoint_type": "compatibility_stub",
        "supported_mode": "mock_smoke_package",
        "canonical_runner": "scripts/run_ai_company_task_harness.py",
        "default_mode": "strict",
        "profiles": ["research_agent", "risk_reviewer", "decision_agent", "synthesis_agent", "reviewer_worker"],
        "note": "The canonical runner emits profile metadata in ai_company/task_harness_report.json."
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
