#!/usr/bin/env python3
"""Compatibility watchdog entry for the published mock package."""

from __future__ import annotations

import json
import sys


def main() -> int:
    print(json.dumps({"watchdog_status": "healthy", "last_action": "none", "repair_attempts_used": 0}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
