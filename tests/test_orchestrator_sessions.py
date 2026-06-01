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


async def test_worker_event_persistence_used_by_sse_does_not_raise_name_error():
    session_id = "session-sse"
    main.insert_session(session_id)

    main._persist_worker_event(session_id, "ready", {"ok": True})

    history = main.get_session_history(session_id)
    assert history is not None
    assert history["events"][0]["event"] == "ready"
    assert history["events"][0]["data"] == {"ok": True}


async def test_worker_status_timeout_does_not_mark_running_session_failed(monkeypatch):
    session_id = "session-timeout"
    session = main.SessionState(
        session_id=session_id,
        worker=WorkerInfo(
            name="worker-timeout",
            host="127.0.0.1",
            vnc=5900,
            novnc=6080,
            streamlit=8501,
            http=8080,
        ),
    )
    main.SESSIONS[session_id] = session
    main.insert_session(session_id)
    main.update_session_status(session_id, "running")
    monkeypatch.setattr(main, "WORKER_STATUS_POLL_SECONDS", 0.01)

    class FakeResponse:
        def raise_for_status(self):
            return None

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, *_args, **_kwargs):
            return FakeResponse()

    async def timeout_status(_session):
        raise main.httpx.ReadTimeout("status timed out")

    monkeypatch.setattr(main.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(main, "_get_worker_status", timeout_status)

    task = asyncio.create_task(main._run_worker_message(session_id, session, "hello"))
    await asyncio.sleep(0.05)
    main._persist_worker_event(session_id, "done", {"ok": True})
    await task

    history = main.get_session_history(session_id)
    assert history is not None
    assert history["session"]["status"] == "completed"
    assert history["session"]["error"] is None
