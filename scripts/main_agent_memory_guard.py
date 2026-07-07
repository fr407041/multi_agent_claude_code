#!/usr/bin/env python3
"""Compatibility memory guard entry for the published mock package."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULTS_PATH = ROOT / "configs" / "ai_company" / "task_harness.defaults.json"


def read_defaults() -> dict:
    try:
        return json.loads(DEFAULTS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def compatibility_payload() -> dict:
    defaults = read_defaults()
    checkpoint_threshold = int(defaults.get("main_agent_memory_token_threshold", 24000))
    hard_threshold = int(defaults.get("main_agent_memory_hard_threshold", 32000))
    return {
        "entrypoint_type": "compatibility_stub",
        "supported_mode": "mock_smoke_package",
        "canonical_runner": "scripts/run_ai_company_task_harness.py",
        "memory_guard_enabled": bool(defaults.get("main_agent_memory_guard_enabled", True)),
        "checkpoint_threshold_tokens": checkpoint_threshold,
        "hard_threshold_tokens": hard_threshold,
        "threshold_source": "configs/ai_company/task_harness.defaults.json",
        "memory_checkpoint_count": 0,
        "checkpoint_decision": "skipped_below_threshold",
        "checkpoint_policy_result": "not_needed_below_threshold",
        "note": "The canonical runner writes ai_company/main_agent_memory_guard_report.json for real mock runs."
    }


def main() -> int:
    print(json.dumps(compatibility_payload(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
