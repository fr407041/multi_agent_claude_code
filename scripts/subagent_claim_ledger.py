#!/usr/bin/env python3
"""Compatibility claim ledger entry for the published mock package."""

from __future__ import annotations

import json
import sys


def main() -> int:
    print(json.dumps({"claim_contract": "accepted tasks require at least one claim with evidence_refs"}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
