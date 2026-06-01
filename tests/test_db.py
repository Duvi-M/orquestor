import sqlite3

from computer_use_demo.api import db


def test_init_db_uses_configurable_path(tmp_path, monkeypatch):
    db_path = tmp_path / "nested" / "orchestrator.db"
    monkeypatch.setenv("COMPUTER_USE_DB_PATH", str(db_path))

    db.init_db()

    assert db_path.exists()

    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

    assert {"sessions", "messages", "events"}.issubset(tables)
    assert {"status", "error", "completed_at"}.issubset(
        {
            row[1]
            for row in conn.execute("PRAGMA table_info(sessions)")
        }
    )


def test_session_history_persists_messages_events_and_status(tmp_path, monkeypatch):
    db_path = tmp_path / "orchestrator.db"
    monkeypatch.setenv("COMPUTER_USE_DB_PATH", str(db_path))

    db.init_db()
    db.insert_session("session-1")
    db.update_session_status("session-1", "running")
    db.insert_message("session-1", "user", "hello")
    db.insert_message("session-1", "assistant", "hi")
    db.insert_event("session-1", "done", {"ok": True})
    db.update_session_status("session-1", "completed", completed=True)

    history = db.get_session_history("session-1")

    assert history is not None
    assert history["session"]["status"] == "completed"
    assert [message["role"] for message in history["messages"]] == ["user", "assistant"]
    assert history["events"][0]["event"] == "done"
    assert history["events"][0]["data"] == {"ok": True}
