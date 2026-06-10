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

    assert {
        "users",
        "organizations",
        "organization_memberships",
        "sessions",
        "messages",
        "events",
    }.issubset(tables)
    assert {"status", "error", "stop_reason", "completed_at"}.issubset(
        {
            row[1]
            for row in conn.execute("PRAGMA table_info(sessions)")
        }
    )
    assert {"user_id", "organization_id"}.issubset(
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
    assert history["session"]["user_id"] == "dev-user"
    assert history["session"]["organization_id"] == "dev-org"
    assert [message["role"] for message in history["messages"]] == ["user", "assistant"]
    assert history["events"][0]["event"] == "done"
    assert history["events"][0]["data"] == {"ok": True}


def test_insert_session_stores_owner(tmp_path, monkeypatch):
    db_path = tmp_path / "owners.db"
    monkeypatch.setenv("COMPUTER_USE_DB_PATH", str(db_path))

    db.init_db()
    db.insert_session("session-owned", user_id="user-a", organization_id="org-a")

    owner = db.get_session_owner("session-owned")
    history = db.get_session_history("session-owned")

    assert owner == {
        "id": "session-owned",
        "user_id": "user-a",
        "organization_id": "org-a",
    }
    assert history is not None
    assert history["session"]["user_id"] == "user-a"
    assert history["session"]["organization_id"] == "org-a"
