#!/usr/bin/env python3
"""Compatibility entry for reviewer worker.

The standalone mock harness writes reviewer_verdicts.json directly so the first
run has no external dependencies.
"""

from __future__ import annotations

import json
import sys


def main() -> int:
    print(json.dumps({"reviewer_status": "compatibility_entry", "verdict": "see task_harness_report.json"}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
