from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from computer_use_demo.api.config import get_settings


def get_db_path() -> Path:
    return get_settings().computer_use_db_path


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
    CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        created_at REAL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS organizations (
        id TEXT PRIMARY KEY,
        created_at REAL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS organization_memberships (
        user_id TEXT NOT NULL,
        organization_id TEXT NOT NULL,
        role TEXT DEFAULT 'member',
        created_at REAL,
        PRIMARY KEY (user_id, organization_id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY,
        user_id TEXT,
        organization_id TEXT,
        created_at REAL,
        last_activity REAL,
        status TEXT DEFAULT 'created',
        error TEXT,
        stop_reason TEXT,
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
    settings = get_settings()
    ensure_identity(settings.dev_user_id, settings.dev_org_id, conn=conn)
    _backfill_session_owners(conn, settings.dev_user_id, settings.dev_org_id)
    conn.close()


def _ensure_session_columns(conn: sqlite3.Connection) -> None:
    existing = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(sessions)")
    }
    columns = {
        "user_id": "TEXT",
        "organization_id": "TEXT",
        "status": "TEXT DEFAULT 'created'",
        "error": "TEXT",
        "stop_reason": "TEXT",
        "completed_at": "REAL",
    }
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE sessions ADD COLUMN {name} {definition}")
    conn.commit()


def _backfill_session_owners(
    conn: sqlite3.Connection,
    default_user_id: str,
    default_org_id: str,
) -> None:
    conn.execute(
        "UPDATE sessions SET user_id = ? WHERE user_id IS NULL OR user_id = ''",
        (default_user_id,),
    )
    conn.execute(
        """
        UPDATE sessions
        SET organization_id = ?
        WHERE organization_id IS NULL OR organization_id = ''
        """,
        (default_org_id,),
    )
    conn.commit()


def ensure_identity(
    user_id: str,
    organization_id: str,
    *,
    conn: sqlite3.Connection | None = None,
) -> None:
    owns_conn = conn is None
    if conn is None:
        conn = get_conn()
    now = time.time()
    conn.execute(
        "INSERT OR IGNORE INTO users (id, created_at) VALUES (?, ?)",
        (user_id, now),
    )
    conn.execute(
        "INSERT OR IGNORE INTO organizations (id, created_at) VALUES (?, ?)",
        (organization_id, now),
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO organization_memberships
            (user_id, organization_id, role, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (user_id, organization_id, "member", now),
    )
    conn.commit()
    if owns_conn:
        conn.close()


def insert_session(
    session_id: str,
    user_id: str | None = None,
    organization_id: str | None = None,
) -> None:
    now = time.time()
    settings = get_settings()
    user_id = user_id or settings.dev_user_id
    organization_id = organization_id or settings.dev_org_id
    conn = get_conn()
    ensure_identity(user_id, organization_id, conn=conn)
    conn.execute(
        """
        INSERT INTO sessions
            (id, user_id, organization_id, created_at, last_activity, status)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (session_id, user_id, organization_id, now, now, "created"),
    )
    conn.commit()
    conn.close()


def get_session_owner(session_id: str) -> dict[str, Any] | None:
    conn = get_conn()
    row = conn.execute(
        """
        SELECT id, user_id, organization_id
        FROM sessions
        WHERE id = ?
        """,
        (session_id,),
    ).fetchone()
    conn.close()
    return None if row is None else dict(row)


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
    stop_reason: str | None = None,
) -> None:
    conn = get_conn()
    conn.execute(
        """
        UPDATE sessions
        SET
            status = ?,
            error = ?,
            stop_reason = COALESCE(?, stop_reason),
            completed_at = CASE WHEN ? THEN ? ELSE completed_at END
        WHERE id = ?
        """,
        (status, error, stop_reason, completed, time.time(), session_id),
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


def count_session_messages(session_id: str, role: str | None = None) -> int:
    conn = get_conn()
    if role is None:
        row = conn.execute(
            "SELECT COUNT(*) AS count FROM messages WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT COUNT(*) AS count FROM messages WHERE session_id = ? AND role = ?",
            (session_id, role),
        ).fetchone()
    conn.close()
    return int(row["count"])


def count_session_events(session_id: str) -> int:
    conn = get_conn()
    row = conn.execute(
        "SELECT COUNT(*) AS count FROM events WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    conn.close()
    return int(row["count"])


def get_session_record(session_id: str) -> dict[str, Any] | None:
    conn = get_conn()
    row = conn.execute(
        """
        SELECT id, user_id, organization_id, created_at, last_activity, status,
               error, stop_reason, completed_at
        FROM sessions
        WHERE id = ?
        """,
        (session_id,),
    ).fetchone()
    conn.close()
    return None if row is None else dict(row)


def get_session_history(session_id: str) -> dict[str, Any] | None:
    conn = get_conn()
    session = conn.execute(
        """
        SELECT id, user_id, organization_id, created_at, last_activity, status,
               error, stop_reason, completed_at
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
