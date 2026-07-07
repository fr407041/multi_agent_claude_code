#!/usr/bin/env python3
"""Compatibility profile resolver entry for the published mock package."""

from __future__ import annotations

import json
import sys


def main() -> int:
    print(json.dumps({
        "default_mode": "strict",
        "profiles": ["research_agent", "risk_reviewer", "decision_agent", "synthesis_agent", "reviewer_worker"]
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
