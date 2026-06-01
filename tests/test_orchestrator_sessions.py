import asyncio

import pytest
from fastapi import HTTPException

from computer_use_demo.api import main
from computer_use_demo.api.worker_manager import WorkerInfo


@pytest.fixture(autouse=True)
def clean_sessions(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("COMPUTER_USE_DB_PATH", str(tmp_path / "orchestrator.db"))
    main.SESSIONS.clear()
    main.init_db()
    yield
    for session in main.SESSIONS.values():
        if session.task and not session.task.done():
            session.task.cancel()
    main.SESSIONS.clear()


async def test_session_creation_uses_isolated_workers(monkeypatch):
    counter = 0

    def fake_start_worker(*, session_id, api_key):
        nonlocal counter
        counter += 1
        return WorkerInfo(
            name=f"worker-{counter}",
            host="127.0.0.1",
            vnc=5900 + counter,
            novnc=6080 + counter,
            streamlit=8501 + counter,
            http=8080 + counter,
        )

    async def fake_wait_worker_ready(_host, _port):
        return None

    monkeypatch.setattr(main, "start_worker", fake_start_worker)
    monkeypatch.setattr(main, "_wait_worker_ready", fake_wait_worker_ready)

    first = await main.create_session()
    second = await main.create_session()

    assert first["session_id"] != second["session_id"]
    assert len(main.SESSIONS) == 2
    assert first["novnc_url"] != second["novnc_url"]


async def test_one_active_task_per_session(monkeypatch):
    session_id = "session-1"
    main.SESSIONS[session_id] = main.SessionState(
        session_id=session_id,
        worker=WorkerInfo(
            name="worker-1",
            host="127.0.0.1",
            vnc=5900,
            novnc=6080,
            streamlit=8501,
            http=8080,
        ),
    )
    main.insert_session(session_id)

    async def slow_worker_message(_session_id, _session, _text):
        await asyncio.sleep(0.2)

    monkeypatch.setattr(main, "_run_worker_message", slow_worker_message)

    result = await main.post_message(session_id, main.UserMessageIn(text="first"))
    assert result["status"] == "running"

    with pytest.raises(HTTPException) as exc:
        await main.post_message(session_id, main.UserMessageIn(text="second"))

    assert exc.value.status_code == 409

    await main.SESSIONS[session_id].task
