from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

DEFAULT_DB_PATH = Path("data") / "orchestrator.db"


def get_db_path() -> Path:
    return Path(os.getenv("COMPUTER_USE_DB_PATH", DEFAULT_DB_PATH)).expanduser()


def get_conn() -> sqlite3.Connection:
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY,
        created_at REAL,
        last_activity REAL,
        status TEXT DEFAULT 'created',
        error TEXT,
        completed_at REAL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT,
        role TEXT,
        text TEXT,
        ts REAL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT,
        event TEXT,
        data TEXT,
        ts REAL
    )
    """)

    conn.commit()
    _ensure_session_columns(conn)
    conn.close()


def _ensure_session_columns(conn: sqlite3.Connection) -> None:
    existing = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(sessions)")
    }
    columns = {
        "status": "TEXT DEFAULT 'created'",
        "error": "TEXT",
        "completed_at": "REAL",
    }
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE sessions ADD COLUMN {name} {definition}")
    conn.commit()


def insert_session(session_id: str) -> None:
    now = time.time()
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO sessions (id, created_at, last_activity, status)
        VALUES (?, ?, ?, ?)
        """,
        (session_id, now, now, "created"),
    )
    conn.commit()
    conn.close()


def update_session_activity(session_id: str) -> None:
    conn = get_conn()
    conn.execute(
        "UPDATE sessions SET last_activity = ? WHERE id = ?",
        (time.time(), session_id),
    )
    conn.commit()
    conn.close()


def update_session_status(
    session_id: str,
    status: str,
    error: str | None = None,
    completed: bool = False,
) -> None:
    conn = get_conn()
    conn.execute(
        """
        UPDATE sessions
        SET status = ?, error = ?, completed_at = CASE WHEN ? THEN ? ELSE completed_at END
        WHERE id = ?
        """,
        (status, error, completed, time.time(), session_id),
    )
    conn.commit()
    conn.close()


def insert_message(session_id: str, role: str, text: str) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO messages (session_id, role, text, ts) VALUES (?, ?, ?, ?)",
        (session_id, role, text, time.time()),
    )
    conn.commit()
    conn.close()


def get_session_history(session_id: str) -> dict[str, Any] | None:
    conn = get_conn()
    session = conn.execute(
        """
        SELECT id, created_at, last_activity, status, error, completed_at
        FROM sessions
        WHERE id = ?
        """,
        (session_id,),
    ).fetchone()

    if session is None:
        conn.close()
        return None

    messages = [
        dict(row)
        for row in conn.execute(
            """
            SELECT id, role, text, ts
            FROM messages
            WHERE session_id = ?
            ORDER BY id ASC
            """,
            (session_id,),
        )
    ]
    events = []
    for row in conn.execute(
        """
        SELECT id, event, data, ts
        FROM events
        WHERE session_id = ?
        ORDER BY id ASC
        """,
        (session_id,),
    ):
        item = dict(row)
        try:
            item["data"] = json.loads(item["data"])
        except (TypeError, json.JSONDecodeError):
            item["data"] = {"raw": item["data"]}
        events.append(item)

    conn.close()
    return {
        "session": dict(session),
        "messages": messages,
        "events": events,
    }


def insert_event(session_id: str, event: str, data: dict[str, Any]) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO events (session_id, event, data, ts) VALUES (?, ?, ?, ?)",
        (session_id, event, json.dumps(data), time.time()),
    )
    conn.commit()
    conn.close()
