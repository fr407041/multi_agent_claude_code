#!/usr/bin/env python3
"""Compatibility reviewer entry for the published mock package."""

from __future__ import annotations

import json
import sys


def main() -> int:
    payload = {
        "entrypoint_type": "compatibility_stub",
        "supported_mode": "mock_smoke_package",
        "canonical_runner": "scripts/run_ai_company_task_harness.py",
        "reviewer_status": "compatibility_stub_only",
        "verdict": "see ai_company/reviewer_verdicts.json from the canonical runner",
        "note": "The standalone mock harness writes reviewer verdicts directly for the first-run smoke test."
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
