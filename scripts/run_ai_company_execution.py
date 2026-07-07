#!/usr/bin/env python3
"""Compatibility execution entry for the published mock package."""

from __future__ import annotations

import json
import sys


def main() -> int:
    payload = {
        "entrypoint_type": "compatibility_stub",
        "supported_mode": "mock_smoke_package",
        "canonical_runner": "scripts/run_ai_company_task_harness.py",
        "execution_status": "compatibility_stub_only",
        "live_required": "Claude Code Router and a tested /v1/messages route",
        "note": "Use the canonical runner for the dependency-free mock flow. Real live execution is tracked by issue #3."
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
