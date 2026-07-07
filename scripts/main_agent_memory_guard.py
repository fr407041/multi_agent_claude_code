#!/usr/bin/env python3
"""Compatibility memory guard entry for the published mock package."""

from __future__ import annotations

import json
import sys


def main() -> int:
    print(json.dumps({
        "memory_guard_enabled": True,
        "memory_checkpoint_count": 0,
        "note": "The standalone mock harness writes main_agent_memory_guard_report.json."
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
