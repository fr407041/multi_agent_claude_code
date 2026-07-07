#!/usr/bin/env python3
"""Compatibility watchdog entry for the published mock package."""

from __future__ import annotations

import json
import sys


def main() -> int:
    payload = {
        "entrypoint_type": "compatibility_stub",
        "supported_mode": "mock_smoke_package",
        "canonical_runner": "scripts/run_ai_company_task_harness.py",
        "watchdog_status": "healthy",
        "last_action": "none",
        "repair_attempts_used": 0,
        "note": "Use the canonical runner to produce ai_company/watchdog_report.json for a mock run."
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
