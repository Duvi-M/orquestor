from httpx import ASGITransport, AsyncClient

from computer_use_demo.api import main
from computer_use_demo.api.main import _parse_sse_block, app
from computer_use_demo.api.worker_manager import WorkerInfo


def _fake_worker(session_id: str, suffix: int = 1) -> WorkerInfo:
    return WorkerInfo(
        name=f"worker-{session_id}",
        host="127.0.0.1",
        vnc=5900 + suffix,
        novnc=6080 + suffix,
        streamlit=8501 + suffix,
        http=8080 + suffix,
    )


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


async def test_default_dev_identity_can_create_session(tmp_path, monkeypatch):
    monkeypatch.delenv("ORCHESTRATOR_API_TOKEN", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("COMPUTER_USE_DB_PATH", str(tmp_path / "default-identity.db"))
    main.SESSIONS.clear()
    main.init_db()

    def fake_start_worker(*, session_id, api_key):
        return _fake_worker(session_id)

    async def fake_wait_worker_ready(_worker):
        return None

    monkeypatch.setattr(main, "start_worker", fake_start_worker)
    monkeypatch.setattr(main, "_wait_worker_ready", fake_wait_worker_ready)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://orchestrator") as client:
        response = await client.post("/sessions")

    assert response.status_code == 200
    session_id = response.json()["session_id"]
    owner = main.get_session_owner(session_id)
    assert owner is not None
    assert owner["user_id"] == "dev-user"
    assert owner["organization_id"] == "dev-org"
    main.SESSIONS.clear()


async def test_session_owner_can_access_session(tmp_path, monkeypatch):
    monkeypatch.delenv("ORCHESTRATOR_API_TOKEN", raising=False)
    monkeypatch.setenv("COMPUTER_USE_DB_PATH", str(tmp_path / "same-owner.db"))
    main.SESSIONS.clear()
    main.init_db()
    session_id = "session-owner-ok"
    main.insert_session(session_id, user_id="user-a", organization_id="org-a")
    main.SESSIONS[session_id] = main.SessionState(session_id=session_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://orchestrator") as client:
        response = await client.get(
            f"/sessions/{session_id}",
            headers={"X-User-Id": "user-a", "X-Org-Id": "org-a"},
        )

    assert response.status_code == 200
    assert response.json()["session_id"] == session_id
    main.SESSIONS.clear()


async def test_different_user_org_cannot_get_session(tmp_path, monkeypatch):
    monkeypatch.delenv("ORCHESTRATOR_API_TOKEN", raising=False)
    monkeypatch.setenv("COMPUTER_USE_DB_PATH", str(tmp_path / "get-denied.db"))
    main.SESSIONS.clear()
    main.init_db()
    session_id = "session-get-denied"
    main.insert_session(session_id, user_id="user-a", organization_id="org-a")
    main.SESSIONS[session_id] = main.SessionState(session_id=session_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://orchestrator") as client:
        response = await client.get(
            f"/sessions/{session_id}",
            headers={"X-User-Id": "user-b", "X-Org-Id": "org-b"},
        )

    assert response.status_code == 404
    main.SESSIONS.clear()


async def test_different_user_org_cannot_send_message(tmp_path, monkeypatch):
    monkeypatch.delenv("ORCHESTRATOR_API_TOKEN", raising=False)
    monkeypatch.setenv("COMPUTER_USE_DB_PATH", str(tmp_path / "message-denied.db"))
    main.SESSIONS.clear()
    main.init_db()
    session_id = "session-message-denied"
    main.insert_session(session_id, user_id="user-a", organization_id="org-a")
    main.SESSIONS[session_id] = main.SessionState(
        session_id=session_id,
        worker=_fake_worker(session_id),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://orchestrator") as client:
        response = await client.post(
            f"/sessions/{session_id}/messages",
            json={"text": "hello"},
            headers={"X-User-Id": "user-b", "X-Org-Id": "org-b"},
        )

    assert response.status_code == 404
    main.SESSIONS.clear()


async def test_different_user_org_cannot_get_history(tmp_path, monkeypatch):
    monkeypatch.delenv("ORCHESTRATOR_API_TOKEN", raising=False)
    monkeypatch.setenv("COMPUTER_USE_DB_PATH", str(tmp_path / "history-denied.db"))
    main.SESSIONS.clear()
    main.init_db()
    session_id = "session-history-denied"
    main.insert_session(session_id, user_id="user-a", organization_id="org-a")
    main.SESSIONS[session_id] = main.SessionState(session_id=session_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://orchestrator") as client:
        response = await client.get(
            f"/sessions/{session_id}/history",
            headers={"X-User-Id": "user-b", "X-Org-Id": "org-b"},
        )

    assert response.status_code == 404
    main.SESSIONS.clear()


async def test_different_user_org_cannot_connect_sse(tmp_path, monkeypatch):
    monkeypatch.delenv("ORCHESTRATOR_API_TOKEN", raising=False)
    monkeypatch.setenv("COMPUTER_USE_DB_PATH", str(tmp_path / "sse-denied.db"))
    main.SESSIONS.clear()
    main.init_db()
    session_id = "session-sse-denied"
    main.insert_session(session_id, user_id="user-a", organization_id="org-a")
    main.SESSIONS[session_id] = main.SessionState(
        session_id=session_id,
        worker=_fake_worker(session_id),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://orchestrator") as client:
        response = await client.get(
            f"/sessions/{session_id}/events",
            headers={"X-User-Id": "user-b", "X-Org-Id": "org-b"},
        )

    assert response.status_code == 404
    main.SESSIONS.clear()


async def test_different_user_org_cannot_delete_session(tmp_path, monkeypatch):
    monkeypatch.delenv("ORCHESTRATOR_API_TOKEN", raising=False)
    monkeypatch.setenv("COMPUTER_USE_DB_PATH", str(tmp_path / "delete-denied.db"))
    main.SESSIONS.clear()
    main.init_db()
    session_id = "session-delete-denied"
    main.insert_session(session_id, user_id="user-a", organization_id="org-a")
    main.SESSIONS[session_id] = main.SessionState(
        session_id=session_id,
        worker=_fake_worker(session_id),
    )
    stopped = []
    monkeypatch.setattr(main, "stop_worker", lambda name: stopped.append(name))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://orchestrator") as client:
        response = await client.delete(
            f"/sessions/{session_id}",
            headers={"X-User-Id": "user-b", "X-Org-Id": "org-b"},
        )

    assert response.status_code == 404
    assert stopped == []
    assert session_id in main.SESSIONS
    main.SESSIONS.clear()


async def test_user_concurrent_session_limit(tmp_path, monkeypatch):
    monkeypatch.delenv("ORCHESTRATOR_API_TOKEN", raising=False)
    monkeypatch.setenv("COMPUTER_USE_DB_PATH", str(tmp_path / "user-limit.db"))
    monkeypatch.setenv("MAX_CONCURRENT_SESSIONS_PER_USER", "1")
    monkeypatch.setenv("MAX_CONCURRENT_SESSIONS_PER_ORG", "10")
    main.SESSIONS.clear()
    main.init_db()
    existing_session_id = "session-user-limit"
    main.insert_session(existing_session_id, user_id="user-a", organization_id="org-a")
    main.update_session_status(existing_session_id, "ready")
    main.SESSIONS[existing_session_id] = main.SessionState(session_id=existing_session_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://orchestrator") as client:
        response = await client.post(
            "/sessions",
            headers={"X-User-Id": "user-a", "X-Org-Id": "org-b"},
        )

    assert response.status_code == 429
    assert "User concurrent session limit exceeded" in response.json()["detail"]
    main.SESSIONS.clear()


async def test_org_concurrent_session_limit(tmp_path, monkeypatch):
    monkeypatch.delenv("ORCHESTRATOR_API_TOKEN", raising=False)
    monkeypatch.setenv("COMPUTER_USE_DB_PATH", str(tmp_path / "org-limit.db"))
    monkeypatch.setenv("MAX_CONCURRENT_SESSIONS_PER_USER", "10")
    monkeypatch.setenv("MAX_CONCURRENT_SESSIONS_PER_ORG", "1")
    main.SESSIONS.clear()
    main.init_db()
    existing_session_id = "session-org-limit"
    main.insert_session(existing_session_id, user_id="user-a", organization_id="org-a")
    main.update_session_status(existing_session_id, "ready")
    main.SESSIONS[existing_session_id] = main.SessionState(session_id=existing_session_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://orchestrator") as client:
        response = await client.post(
            "/sessions",
            headers={"X-User-Id": "user-b", "X-Org-Id": "org-a"},
        )

    assert response.status_code == 429
    assert "Organization concurrent session limit exceeded" in response.json()["detail"]
    main.SESSIONS.clear()


async def test_max_messages_per_session_limit(tmp_path, monkeypatch):
    monkeypatch.delenv("ORCHESTRATOR_API_TOKEN", raising=False)
    monkeypatch.setenv("COMPUTER_USE_DB_PATH", str(tmp_path / "message-limit.db"))
    monkeypatch.setenv("MAX_MESSAGES_PER_SESSION", "1")
    main.SESSIONS.clear()
    main.init_db()
    session_id = "session-message-limit"
    main.insert_session(session_id, user_id="user-a", organization_id="org-a")
    main.update_session_status(session_id, "ready")
    main.insert_message(session_id, "user", "first")
    main.SESSIONS[session_id] = main.SessionState(
        session_id=session_id,
        worker=_fake_worker(session_id),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://orchestrator") as client:
        response = await client.post(
            f"/sessions/{session_id}/messages",
            json={"text": "second"},
            headers={"X-User-Id": "user-a", "X-Org-Id": "org-a"},
        )

    assert response.status_code == 429
    assert response.json()["detail"] == "Session message limit exceeded"
    history = main.get_session_history(session_id)
    assert history is not None
    assert history["events"][0]["event"] == "quota_exceeded"
    main.SESSIONS.clear()


async def test_platform_kill_switch_rejects_new_sessions(tmp_path, monkeypatch):
    monkeypatch.delenv("ORCHESTRATOR_API_TOKEN", raising=False)
    monkeypatch.setenv("COMPUTER_USE_DB_PATH", str(tmp_path / "kill-switch.db"))
    monkeypatch.setenv("GLOBAL_KILL_SWITCH", "true")
    main.SESSIONS.clear()
    main.init_db()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://orchestrator") as client:
        response = await client.post("/sessions")

    assert response.status_code == 403
    assert response.json()["detail"] == "Global kill switch is enabled"
    main.SESSIONS.clear()


async def test_platform_kill_switch_rejects_messages(tmp_path, monkeypatch):
    monkeypatch.delenv("ORCHESTRATOR_API_TOKEN", raising=False)
    monkeypatch.setenv("COMPUTER_USE_DB_PATH", str(tmp_path / "message-kill-switch.db"))
    monkeypatch.setenv("GLOBAL_KILL_SWITCH", "true")
    main.SESSIONS.clear()
    main.init_db()
    session_id = "session-message-kill"
    main.insert_session(session_id, user_id="user-a", organization_id="org-a")
    main.update_session_status(session_id, "ready")
    main.SESSIONS[session_id] = main.SessionState(
        session_id=session_id,
        worker=_fake_worker(session_id),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://orchestrator") as client:
        response = await client.post(
            f"/sessions/{session_id}/messages",
            json={"text": "hello"},
            headers={"X-User-Id": "user-a", "X-Org-Id": "org-a"},
        )

    assert response.status_code == 403
    assert response.json()["detail"] == "Global kill switch is enabled"
    main.SESSIONS.clear()


async def test_runtime_expired_session_stops_worker_and_rejects_message(tmp_path, monkeypatch):
    monkeypatch.delenv("ORCHESTRATOR_API_TOKEN", raising=False)
    monkeypatch.setenv("COMPUTER_USE_DB_PATH", str(tmp_path / "runtime-expired.db"))
    monkeypatch.setenv("MAX_SESSION_RUNTIME_SECONDS", "1")
    main.SESSIONS.clear()
    main.init_db()
    session_id = "session-runtime-expired"
    main.insert_session(session_id, user_id="user-a", organization_id="org-a")
    main.update_session_status(session_id, "ready")
    conn = main.get_conn()
    conn.execute(
        "UPDATE sessions SET created_at = created_at - 10 WHERE id = ?",
        (session_id,),
    )
    conn.commit()
    conn.close()
    main.SESSIONS[session_id] = main.SessionState(
        session_id=session_id,
        worker=_fake_worker(session_id),
    )
    stopped = []
    monkeypatch.setattr(main, "stop_worker", lambda name: stopped.append(name))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://orchestrator") as client:
        response = await client.post(
            f"/sessions/{session_id}/messages",
            json={"text": "hello"},
            headers={"X-User-Id": "user-a", "X-Org-Id": "org-a"},
        )

    assert response.status_code == 429
    assert response.json()["detail"] == "Session runtime limit exceeded"
    assert stopped == [f"worker-{session_id}"]
    assert session_id not in main.SESSIONS
    history = main.get_session_history(session_id)
    assert history is not None
    assert history["session"]["status"] == "expired"
    assert history["session"]["stop_reason"] == "runtime_limit_exceeded"
    assert history["events"][0]["event"] == "session_expired"
    main.SESSIONS.clear()


async def test_expired_session_cannot_accept_message(tmp_path, monkeypatch):
    monkeypatch.delenv("ORCHESTRATOR_API_TOKEN", raising=False)
    monkeypatch.setenv("COMPUTER_USE_DB_PATH", str(tmp_path / "already-expired.db"))
    main.SESSIONS.clear()
    main.init_db()
    session_id = "session-already-expired"
    main.insert_session(session_id, user_id="user-a", organization_id="org-a")
    main.update_session_status(
        session_id,
        "expired",
        completed=True,
        stop_reason="idle_limit_exceeded",
    )
    main.SESSIONS[session_id] = main.SessionState(
        session_id=session_id,
        worker=_fake_worker(session_id),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://orchestrator") as client:
        response = await client.post(
            f"/sessions/{session_id}/messages",
            json={"text": "hello"},
            headers={"X-User-Id": "user-a", "X-Org-Id": "org-a"},
        )

    assert response.status_code == 429
    assert response.json()["detail"] == "Session has expired"
    main.SESSIONS.clear()
