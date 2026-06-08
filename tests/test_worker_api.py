import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

pytest.importorskip("anthropic")

from computer_use_demo import worker_api  # noqa: E402


@pytest.fixture(autouse=True)
def reset_worker_state(monkeypatch):
    worker_api.STATE.queue = asyncio.Queue()
    worker_api.STATE.task = None
    worker_api.STATE.messages = []
    worker_api.STATE.status = "idle"
    worker_api.STATE.error = None
    worker_api.STATE.next_event_id = 1
    worker_api.STATE.event_log = []
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("TOOL_VERSION", "computer_use_20250124")
    yield
    if worker_api.STATE.task and not worker_api.STATE.task.done():
        worker_api.STATE.task.cancel()


async def _wait_for_worker_idle():
    for _ in range(20):
        if not worker_api.STATE.task or worker_api.STATE.task.done():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("worker task did not finish")


async def test_worker_messages_calls_sampling_loop_before_done(monkeypatch):
    called = False

    async def fake_sampling_loop(**kwargs):
        nonlocal called
        called = True
        await asyncio.sleep(0)
        return kwargs["messages"]

    monkeypatch.setattr(worker_api, "sampling_loop", fake_sampling_loop)

    transport = ASGITransport(app=worker_api.app)
    async with AsyncClient(transport=transport, base_url="http://worker") as client:
        response = await client.post("/messages", json={"text": "hello"})

    assert response.status_code == 200
    await _wait_for_worker_idle()

    assert called is True
    assert [event["event"] for event in worker_api.STATE.event_log] == [
        "user_message",
        "done",
    ]


async def test_worker_api_error_emits_error_not_done(monkeypatch):
    async def fake_sampling_loop(**kwargs):
        kwargs["api_response_callback"](None, None, RuntimeError("bad api key"))
        return kwargs["messages"]

    monkeypatch.setattr(worker_api, "sampling_loop", fake_sampling_loop)

    transport = ASGITransport(app=worker_api.app)
    async with AsyncClient(transport=transport, base_url="http://worker") as client:
        response = await client.post("/messages", json={"text": "hello"})

    assert response.status_code == 200
    await _wait_for_worker_idle()

    events = [event["event"] for event in worker_api.STATE.event_log]
    assert "error" in events
    assert "done" not in events
    assert worker_api.STATE.status == "error"
