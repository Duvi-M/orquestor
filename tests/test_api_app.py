from httpx import ASGITransport, AsyncClient

from computer_use_demo.api import main
from computer_use_demo.api.main import _parse_sse_block, app


def test_api_app_imports():
    assert app.title == "Computer Use Backend (Challenge)"


def test_parse_sse_block_with_json_data():
    event, data, event_id = _parse_sse_block(
        'id: 42\nevent: assistant_block\ndata: {"type": "text", "text": "hello"}'
    )

    assert event == "assistant_block"
    assert data == {"type": "text", "text": "hello"}
    assert event_id == "42"


def test_parse_sse_block_with_raw_data():
    event, data, event_id = _parse_sse_block("event: debug\ndata: not json")

    assert event == "debug"
    assert data == {"raw": "not json"}
    assert event_id is None


def test_parse_sse_block_with_multiline_json_data():
    event, data, event_id = _parse_sse_block(
        'id: 7\nevent: tool_result\ndata: {"output": "hello\\nworld"}'
    )

    assert event == "tool_result"
    assert data == {"output": "hello\nworld"}
    assert event_id == "7"


async def test_healthz():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://orchestrator") as client:
        response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "status": "healthy"}


async def test_readyz(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPUTER_USE_DB_PATH", str(tmp_path / "ready.db"))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://orchestrator") as client:
        response = await client.get("/readyz")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["status"] == "ready"


async def test_session_request_passes_when_token_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("ORCHESTRATOR_API_TOKEN", raising=False)
    monkeypatch.setenv("COMPUTER_USE_DB_PATH", str(tmp_path / "open.db"))
    session_id = "session-open"
    main.SESSIONS.clear()
    main.init_db()
    main.insert_session(session_id)
    main.SESSIONS[session_id] = main.SessionState(session_id=session_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://orchestrator") as client:
        response = await client.get(f"/sessions/{session_id}")

    assert response.status_code == 200
    assert response.json()["session_id"] == session_id
    main.SESSIONS.clear()


async def test_session_request_requires_token_when_set(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_API_TOKEN", "test-token")
    monkeypatch.setenv("COMPUTER_USE_DB_PATH", str(tmp_path / "protected.db"))
    session_id = "session-protected"
    main.SESSIONS.clear()
    main.init_db()
    main.insert_session(session_id)
    main.SESSIONS[session_id] = main.SessionState(session_id=session_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://orchestrator") as client:
        response = await client.get(f"/sessions/{session_id}")

    assert response.status_code == 401
    main.SESSIONS.clear()


async def test_session_request_accepts_valid_bearer_token(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_API_TOKEN", "test-token")
    monkeypatch.setenv("COMPUTER_USE_DB_PATH", str(tmp_path / "protected-ok.db"))
    session_id = "session-protected-ok"
    main.SESSIONS.clear()
    main.init_db()
    main.insert_session(session_id)
    main.SESSIONS[session_id] = main.SessionState(session_id=session_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://orchestrator") as client:
        response = await client.get(
            f"/sessions/{session_id}",
            headers={"Authorization": "Bearer test-token"},
        )

    assert response.status_code == 200
    assert response.json()["session_id"] == session_id
    main.SESSIONS.clear()


async def test_healthz_remains_public_when_token_set(monkeypatch):
    monkeypatch.setenv("ORCHESTRATOR_API_TOKEN", "test-token")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://orchestrator") as client:
        response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.json()["ok"] is True
