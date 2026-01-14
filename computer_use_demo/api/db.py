from __future__ import annotations

import sqlite3
import json
import time
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).parent / "data.db"


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY,
        created_at REAL,
        last_activity REAL
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
    conn.close()


# Writes

def insert_session(session_id: str) -> None:
    now = time.time()
    conn = get_conn()
    conn.execute(
        "INSERT INTO sessions (id, created_at, last_activity) VALUES (?, ?, ?)",
        (session_id, now, now),
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


def insert_message(session_id: str, role: str, text: str) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO messages (session_id, role, text, ts) VALUES (?, ?, ?, ?)",
        (session_id, role, text, time.time()),
    )
    conn.commit()
    conn.close()


def insert_event(session_id: str, event: str, data: dict[str, Any]) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO events (session_id, event, data, ts) VALUES (?, ?, ?, ?)",
        (session_id, event, json.dumps(data), time.time()),
    )
    conn.commit()
    conn.close()