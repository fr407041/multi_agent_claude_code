from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path

from app.services.session_store import append_event, append_message, ensure_session, session_history, write_session_artifact


def run_chat(prompt: str, session_id: str | None = None) -> dict[str, str]:
    session_id = session_id or str(uuid.uuid4())
    ensure_session(session_id)
    append_message(session_id, "user", prompt)
    history = session_history(session_id)
    compact_history = "\n".join(f"{item['role']}: {item['content']}" for item in history[-8:])
    append_event(session_id, "turn_started", "main_agent", {"status": "running"})
    command = [os.getenv("AGENT_OS_CLAUDE_BIN", "claude"), "--bare", "-p", "--output-format", "json", "--tools", ""]
    provider_prompt = f"Continue this explicit session conversation. Do not expose hidden reasoning.\n{compact_history}"
    try:
        completed = subprocess.run(command, input=provider_prompt, text=True, capture_output=True, timeout=int(os.getenv("AGENT_OS_CHAT_TIMEOUT_SEC", "180")), check=False)
        if completed.returncode != 0:
            raise RuntimeError((completed.stderr or completed.stdout or "Claude CLI failed")[:1200])
        envelope = json.loads(completed.stdout)
        response = str(envelope.get("result", "")).strip()
        if not response:
            raise RuntimeError("Claude CLI returned an empty result")
        append_message(session_id, "assistant", response)
        artifact = write_session_artifact(session_id, "latest-turn.json", {"prompt": prompt, "response": response})
        append_event(session_id, "turn_completed", "main_agent", {"status": "completed", "artifact": artifact})
        return {"response": response, "session_id": session_id}
    except Exception as exc:
        append_event(session_id, "turn_failed", "main_agent", {"status": "failed", "error": str(exc)})
        raise
