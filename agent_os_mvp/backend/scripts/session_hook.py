#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import urllib.request


def main() -> int:
    event = json.load(sys.stdin)
    session_id = str(event.get("session_id") or os.getenv("AGENT_OS_SESSION_ID", "")).strip()
    if not session_id:
        print("session hook skipped: session_id missing", file=sys.stderr)
        return 2
    payload = {
        "session_id": session_id,
        "event_type": str(event.get("hook_event_name") or event.get("event_type") or "hook_event"),
        "agent_role": str(event.get("agent_role") or "system_hook"),
        "payload": {key: value for key, value in event.items() if key not in {"transcript", "chain_of_thought", "reasoning"}},
    }
    request = urllib.request.Request(
        os.getenv("AGENT_OS_HOOK_URL", "http://127.0.0.1:8010/api/v1/hooks/events"),
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Agent-OS-Hook-Token": os.getenv("AGENT_OS_HOOK_TOKEN", "")},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        response.read()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
