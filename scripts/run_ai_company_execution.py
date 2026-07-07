#!/usr/bin/env python3
"""Compatibility entry for execution stage.

The published first-run smoke path is standalone in run_ai_company_task_harness.py.
Full live execution requires Claude Code Router and the internal worker scripts.
"""

from __future__ import annotations

import json
import sys


def main() -> int:
    print(json.dumps({"execution_status": "compatibility_entry", "live_required": "Claude Code Router"}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
