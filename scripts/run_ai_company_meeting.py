#!/usr/bin/env python3
"""Compatibility entry for the published mock package.

The issue #1 runnable path is implemented by run_ai_company_task_harness.py.
The full internal repository contains the richer meeting engine; this public
entry exists so company users can see the intended module boundary.
"""

from __future__ import annotations

import json
import sys


def main() -> int:
    payload = {
        "meeting_status": "MEETING_READY",
        "note": "Use scripts/run_ai_company_task_harness.py --mode mock for the published smoke demo."
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
