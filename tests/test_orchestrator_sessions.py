import asyncio
from contextlib import suppress

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

    async def fake_wait_worker_ready(_worker):
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


async def test_session_history_endpoint_still_returns_messages_and_events():
    session_id = "session-history"
    main.insert_session(session_id)
    main.SESSIONS[session_id] = main.SessionState(session_id=session_id)
    main.insert_message(session_id, "user", "hello")
    main._persist_worker_event(session_id, "done", {"ok": True})

    history = await main.session_history(session_id)

    assert history["session"]["id"] == session_id
    assert history["messages"][0]["role"] == "user"
    assert history["events"][0]["event"] == "done"


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


async def test_worker_forward_failure_marks_session_error(monkeypatch):
    session_id = "session-forward-failure"
    session = main.SessionState(
        session_id=session_id,
        worker=WorkerInfo(
            name="worker-forward-failure",
            host="127.0.0.1",
            vnc=5900,
            novnc=6080,
            streamlit=8501,
            http=8080,
        ),
    )
    main.SESSIONS[session_id] = session
    main.insert_session(session_id)

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, *_args, **_kwargs):
            raise main.httpx.ConnectError("worker unavailable")

    monkeypatch.setattr(main.httpx, "AsyncClient", FakeAsyncClient)

    await main._run_worker_message(session_id, session, "hello")

    history = main.get_session_history(session_id)
    assert history is not None
    assert history["session"]["status"] == "error"
    assert "worker unavailable" in history["session"]["error"]


async def test_worker_startup_readiness_failure_is_persisted(monkeypatch):
    stopped = []

    def fake_start_worker(*, session_id, api_key):
        return WorkerInfo(
            name=f"worker-{session_id}",
            host="127.0.0.1",
            vnc=5900,
            novnc=6080,
            streamlit=8501,
            http=8080,
        )

    async def fake_wait_worker_ready(worker):
        raise main.WorkerReadyError(f"not ready: {worker.name}")

    def fake_stop_worker(name):
        stopped.append(name)

    monkeypatch.setattr(main, "start_worker", fake_start_worker)
    monkeypatch.setattr(main, "_wait_worker_ready", fake_wait_worker_ready)
    monkeypatch.setattr(main, "stop_worker", fake_stop_worker)

    with pytest.raises(HTTPException) as exc:
        await main.create_session()

    assert exc.value.status_code == 500
    assert len(stopped) == 1

    session_id = stopped[0].removeprefix("worker-")
    sessions = main.get_session_history(session_id)
    assert sessions is not None
    assert sessions["session"]["status"] == "error"
    assert sessions["events"][0]["event"] == "error"
    assert sessions["events"][0]["data"]["stage"] == "worker_readiness"


async def test_delete_session_stops_worker_and_persists_deleted_event(monkeypatch):
    stopped = []
    session_id = "session-delete"
    task_started = asyncio.Event()
    release_task = asyncio.Event()

    async def running_task():
        task_started.set()
        await release_task.wait()

    main.insert_session(session_id)
    main.SESSIONS[session_id] = main.SessionState(
        session_id=session_id,
        worker=WorkerInfo(
            name="worker-delete",
            host="127.0.0.1",
            vnc=5900,
            novnc=6080,
            streamlit=8501,
            http=8080,
        ),
    )
    task = asyncio.create_task(running_task())
    main.SESSIONS[session_id].task = task
    await task_started.wait()

    monkeypatch.setattr(main, "stop_worker", lambda name: stopped.append(name))

    result = await main.delete_session(session_id)

    assert result == {"ok": True}
    assert stopped == ["worker-delete"]
    assert session_id not in main.SESSIONS

    history = main.get_session_history(session_id)
    assert history is not None
    assert history["session"]["status"] == "deleted"
    assert history["events"][0]["event"] == "deleted"
    with suppress(asyncio.CancelledError):
        await task


async def test_sse_reconnect_limit_persists_one_synthetic_error(monkeypatch):
    session_id = "session-sse-limit"
    main.insert_session(session_id)
    main.SESSIONS[session_id] = main.SessionState(
        session_id=session_id,
        worker=WorkerInfo(
            name="worker-sse",
            host="127.0.0.1",
            vnc=5900,
            novnc=6080,
            streamlit=8501,
            http=8080,
        ),
    )
    monkeypatch.setattr(main, "SSE_RETRY_LIMIT", 1)
    monkeypatch.setattr(main, "SSE_RETRY_INITIAL_BACKOFF_SECONDS", 0)
    monkeypatch.setattr(main, "SSE_RETRY_MAX_BACKOFF_SECONDS", 0)

    class FakeStream:
        async def __aenter__(self):
            raise main.httpx.ReadError("boom")

        async def __aexit__(self, *_args):
            return False

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def stream(self, *_args, **_kwargs):
            return FakeStream()

    class FakeRequest:
        headers = {}

    monkeypatch.setattr(main.httpx, "AsyncClient", FakeClient)

    response = await main.sse_events(session_id, FakeRequest())
    chunks = [chunk async for chunk in response.body_iterator]

    assert len(chunks) == 1
    assert "event: error" in chunks[0]

    history = main.get_session_history(session_id)
    assert history is not None
    error_events = [event for event in history["events"] if event["event"] == "error"]
    assert len(error_events) == 1
