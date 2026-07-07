#!/usr/bin/env python3
"""Compatibility meeting entry for the published mock package."""

from __future__ import annotations

import json
import sys


def main() -> int:
    payload = {
        "entrypoint_type": "compatibility_stub",
        "supported_mode": "mock_smoke_package",
        "canonical_runner": "scripts/run_ai_company_task_harness.py",
        "meeting_status": "MEETING_READY",
        "note": "Use the canonical runner to produce ai_company/meeting_decision.json and the full mock artifact set."
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
