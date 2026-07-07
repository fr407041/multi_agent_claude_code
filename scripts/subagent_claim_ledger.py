#!/usr/bin/env python3
"""Compatibility claim ledger entry for the published mock package."""

from __future__ import annotations

import json
import sys


def main() -> int:
    payload = {
        "entrypoint_type": "compatibility_stub",
        "supported_mode": "mock_smoke_package",
        "canonical_runner": "scripts/run_ai_company_task_harness.py",
        "claim_contract": "accepted tasks require at least one claim with evidence_refs",
        "note": "Use the canonical runner to produce ai_company/subagent_claim_ledger.json."
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
