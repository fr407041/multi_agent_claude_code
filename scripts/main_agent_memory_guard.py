#!/usr/bin/env python3
"""Compatibility memory guard entry for the published mock package."""

from __future__ import annotations

import json
import sys


def compatibility_payload() -> dict:
    return {
        "entrypoint_type": "compatibility_stub",
        "supported_mode": "mock_smoke_package",
        "canonical_runner": "scripts/run_ai_company_task_harness.py",
        "memory_guard_enabled": True,
        "checkpoint_threshold_tokens": 8000,
        "memory_checkpoint_count": 0,
        "checkpoint_decision": "skipped_below_threshold",
        "checkpoint_policy_result": "not_needed_below_threshold",
        "note": "The canonical runner writes ai_company/main_agent_memory_guard_report.json for real mock runs."
    }


def main() -> int:
    print(json.dumps(compatibility_payload(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
