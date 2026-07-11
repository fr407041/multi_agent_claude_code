from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from app.db import get_db_path


SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_sessions (session_id TEXT PRIMARY KEY, status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS session_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS session_events (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, event_type TEXT NOT NULL, agent_role TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS session_artifacts (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, path TEXT NOT NULL, kind TEXT NOT NULL, created_at TEXT NOT NULL);
"""

POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_sessions (session_id TEXT PRIMARY KEY, status TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL);
CREATE TABLE IF NOT EXISTS session_messages (id BIGSERIAL PRIMARY KEY, session_id TEXT NOT NULL REFERENCES agent_sessions(session_id), role TEXT NOT NULL, content TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL);
CREATE TABLE IF NOT EXISTS session_events (id BIGSERIAL PRIMARY KEY, session_id TEXT NOT NULL REFERENCES agent_sessions(session_id), event_type TEXT NOT NULL, agent_role TEXT NOT NULL, payload_json JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL);
CREATE TABLE IF NOT EXISTS session_artifacts (id BIGSERIAL PRIMARY KEY, session_id TEXT NOT NULL REFERENCES agent_sessions(session_id), path TEXT NOT NULL, kind TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def database_url() -> str:
    return os.getenv("AGENT_OS_DATABASE_URL", "").strip()


@contextmanager
def session_db() -> Iterator[tuple[Any, bool]]:
    url = database_url()
    if url.startswith(("postgres://", "postgresql://")):
        import psycopg

        with psycopg.connect(url) as connection:
            yield connection, True
        return
    path = get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        yield connection, False
        connection.commit()
    finally:
        connection.close()


def init_session_store() -> None:
    with session_db() as (connection, postgres):
        if postgres:
            with connection.cursor() as cursor:
                cursor.execute(POSTGRES_SCHEMA)
        else:
            connection.executescript(SQLITE_SCHEMA)


def _execute(connection: Any, postgres: bool, sql: str, params: tuple[Any, ...] = ()) -> Any:
    rendered = sql.replace("?", "%s") if postgres else sql
    cursor = connection.cursor()
    cursor.execute(rendered, params)
    return cursor


def ensure_session(session_id: str) -> None:
    now = utc_now()
    with session_db() as (connection, postgres):
        _execute(
            connection, postgres,
            "INSERT INTO agent_sessions(session_id,status,created_at,updated_at) VALUES(?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET updated_at=excluded.updated_at",
            (session_id, "active", now, now),
        )


def append_message(session_id: str, role: str, content: str) -> None:
    with session_db() as (connection, postgres):
        _execute(connection, postgres, "INSERT INTO session_messages(session_id,role,content,created_at) VALUES(?,?,?,?)", (session_id, role, content, utc_now()))


def append_event(session_id: str, event_type: str, agent_role: str, payload: dict[str, Any]) -> None:
    now = utc_now()
    with session_db() as (connection, postgres):
        value: Any = json.dumps(payload, ensure_ascii=False)
        if postgres:
            from psycopg.types.json import Jsonb
            value = Jsonb(payload)
        _execute(connection, postgres, "INSERT INTO session_events(session_id,event_type,agent_role,payload_json,created_at) VALUES(?,?,?,?,?)", (session_id, event_type, agent_role, value, now))
        _execute(connection, postgres, "UPDATE agent_sessions SET status=?,updated_at=? WHERE session_id=?", (payload.get("status", "active"), now, session_id))


def session_history(session_id: str, limit: int = 12) -> list[dict[str, Any]]:
    with session_db() as (connection, postgres):
        rows = _execute(connection, postgres, "SELECT role,content,created_at FROM session_messages WHERE session_id=? ORDER BY id DESC LIMIT ?", (session_id, limit)).fetchall()
    return [dict(row) if not isinstance(row, tuple) else {"role": row[0], "content": row[1], "created_at": str(row[2])} for row in reversed(rows)]


def session_dashboard(session_id: str) -> dict[str, Any]:
    with session_db() as (connection, postgres):
        session = _execute(connection, postgres, "SELECT session_id,status,created_at,updated_at FROM agent_sessions WHERE session_id=?", (session_id,)).fetchone()
        messages = _execute(connection, postgres, "SELECT role,content,created_at FROM session_messages WHERE session_id=? ORDER BY id", (session_id,)).fetchall()
        events = _execute(connection, postgres, "SELECT event_type,agent_role,payload_json,created_at FROM session_events WHERE session_id=? ORDER BY id", (session_id,)).fetchall()
    def row_dict(row: Any, keys: list[str]) -> dict[str, Any]:
        return dict(row) if not isinstance(row, tuple) else dict(zip(keys, row))
    event_rows = [row_dict(row, ["event_type", "agent_role", "payload_json", "created_at"]) for row in events]
    for event in event_rows:
        if isinstance(event.get("payload_json"), str):
            event["payload"] = json.loads(event.pop("payload_json"))
        else:
            event["payload"] = event.pop("payload_json")
    return {
        "session": row_dict(session, ["session_id", "status", "created_at", "updated_at"]) if session else None,
        "messages": [row_dict(row, ["role", "content", "created_at"]) for row in messages],
        "events": event_rows,
        "progress": {"event_count": len(event_rows), "completed": bool(session and row_dict(session, ["session_id", "status", "created_at", "updated_at"])["status"] == "completed")},
    }


def write_session_artifact(session_id: str, name: str, payload: dict[str, Any]) -> str:
    root = Path(os.getenv("AGENT_OS_SESSION_ROOT", "./sessions")).resolve() / session_id
    root.mkdir(parents=True, exist_ok=True)
    target = (root / name).resolve()
    target.relative_to(root)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    with session_db() as (connection, postgres):
        _execute(connection, postgres, "INSERT INTO session_artifacts(session_id,path,kind,created_at) VALUES(?,?,?,?)", (session_id, str(target), "json", utc_now()))
    return str(target)
