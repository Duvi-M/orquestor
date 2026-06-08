from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from computer_use_demo.api.config import ConfigError, get_settings
from computer_use_demo.api.db import (
    get_conn,
    get_session_history,
    init_db,
    insert_event,
    insert_message,
    insert_session,
    update_session_activity,
    update_session_status,
)
from computer_use_demo.api.worker_manager import (
    WorkerInfo,
    cleanup_project_workers,
    start_worker,
    stop_worker,
)

logger = logging.getLogger(__name__)
settings = get_settings()

app = FastAPI(title="Computer Use Backend (Challenge)", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SESSION_TTL_SECONDS = settings.session_ttl_seconds
CLEANUP_EVERY_SECONDS = settings.cleanup_every_seconds

WORKER_READY_TIMEOUT_SECONDS = settings.worker_ready_timeout_seconds
WORKER_READY_POLL_SECONDS = settings.worker_ready_poll_seconds
WORKER_STATUS_POLL_SECONDS = settings.worker_status_poll_seconds
SSE_RETRY_LIMIT = settings.sse_retry_limit
SSE_RETRY_INITIAL_BACKOFF_SECONDS = settings.sse_retry_initial_backoff_seconds
SSE_RETRY_MAX_BACKOFF_SECONDS = settings.sse_retry_max_backoff_seconds


class WorkerReadyError(RuntimeError):
    pass


bearer_scheme = HTTPBearer(auto_error=False)
bearer_dependency = Depends(bearer_scheme)


def require_orchestrator_token(
    credentials: HTTPAuthorizationCredentials | None = bearer_dependency,
) -> None:
    token = get_settings().orchestrator_api_token
    if not token:
        return
    if (
        credentials is None
        or credentials.scheme.lower() != "bearer"
        or not secrets.compare_digest(credentials.credentials, token)
    ):
        raise HTTPException(status_code=401, detail="Invalid or missing bearer token")


class UserMessageIn(BaseModel):
    text: str


class OkResponse(BaseModel):
    ok: bool


class MessageAcceptedResponse(BaseModel):
    ok: bool
    status: str


class SessionCreateResponse(BaseModel):
    session_id: str
    ui_url: str
    novnc_url: str
    streamlit_url: str | None
    legacy_streamlit_enabled: bool
    worker_http: str


class WorkerResponse(BaseModel):
    name: str
    host: str
    vnc: int
    novnc: int
    streamlit: int
    http: int


class SessionResponse(BaseModel):
    session_id: str
    busy: bool
    status: str | None
    worker: WorkerResponse | None


class HealthResponse(BaseModel):
    ok: bool
    status: str


class ReadyResponse(BaseModel):
    ok: bool
    status: str
    db_path: str


@dataclass
class SessionState:
    session_id: str
    task: asyncio.Task | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    completion_event: asyncio.Event = field(default_factory=asyncio.Event)
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    worker: WorkerInfo | None = None


SESSIONS: dict[str, SessionState] = {}


def _touch(s: SessionState) -> None:
    s.last_activity = time.time()
    update_session_activity(s.session_id)


def _get_session(session_id: str) -> SessionState:
    s = SESSIONS.get(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")
    return s


async def _cleanup_sessions_loop() -> None:
    while True:
        now = time.time()
        expired: list[str] = []

        for sid, s in list(SESSIONS.items()):
            busy = bool(s.task and not s.task.done())
            if (not busy) and (now - s.last_activity) > SESSION_TTL_SECONDS:
                expired.append(sid)

        for sid in expired:
            s = SESSIONS.pop(sid, None)
            if s and s.worker:
                logger.info(
                    "session_cleanup_expired session_id=%s worker=%s",
                    sid,
                    s.worker.name,
                )
                stop_worker(s.worker.name)

        await asyncio.sleep(CLEANUP_EVERY_SECONDS)


def _parse_sse_block(block: str) -> tuple[str | None, dict[str, Any] | None, str | None]:
    """
    Parses ONE SSE event block (text between blank lines).
    Returns: (event_name, data_dict, event_id_str)
    """
    event_name: str | None = None
    data_lines: list[str] = []
    event_id: str | None = None

    for line in block.splitlines():
        if line.startswith("id:"):
            event_id = line[len("id:") :].strip()
        elif line.startswith("event:"):
            event_name = line[len("event:") :].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:") :].lstrip())

    if not event_name:
        return None, None, event_id

    raw = "\n".join(data_lines).strip()
    if not raw:
        return event_name, {}, event_id

    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return event_name, parsed, event_id
        return event_name, {"value": parsed}, event_id
    except Exception:
        return event_name, {"raw": raw}, event_id


async def _wait_worker_ready(worker: WorkerInfo) -> None:
    url = f"http://{worker.host}:{worker.http}/health"
    deadline = time.time() + WORKER_READY_TIMEOUT_SECONDS
    last_error = "no response yet"
    logger.info(
        "worker_readiness_check_start worker=%s host=%s http_port=%s novnc_port=%s timeout_seconds=%s",
        worker.name,
        worker.host,
        worker.http,
        worker.novnc,
        WORKER_READY_TIMEOUT_SECONDS,
    )

    async with httpx.AsyncClient(timeout=2.0) as client:
        while time.time() < deadline:
            try:
                r = await client.get(url)
                if r.status_code == 200:
                    logger.info(
                        "worker_readiness_check_ok worker=%s host=%s http_port=%s",
                        worker.name,
                        worker.host,
                        worker.http,
                    )
                    return
                last_error = f"health returned HTTP {r.status_code}"
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            await asyncio.sleep(WORKER_READY_POLL_SECONDS)

    message = (
        "Worker did not become ready in time "
        f"name={worker.name} host={worker.host} http={worker.http} "
        f"novnc={worker.novnc} timeout={WORKER_READY_TIMEOUT_SECONDS}s "
        f"last_error={last_error}"
    )
    logger.error(message)
    raise WorkerReadyError(message)


@app.on_event("startup")
async def startup() -> None:
    logging.basicConfig(level=get_settings().log_level)
    init_db()
    if get_settings().cleanup_orphan_workers_on_startup:
        cleaned = cleanup_project_workers()
        logger.info("startup_orphan_cleanup containers_removed=%s", cleaned)
    logger.info(
        "app_startup app=computer-use-orchestrator db_path=%s cleanup_orphans=%s auth_enabled=%s cors_origins=%s",
        get_settings().computer_use_db_path,
        get_settings().cleanup_orphan_workers_on_startup,
        bool(get_settings().orchestrator_api_token),
        ",".join(get_settings().cors_allowed_origins),
    )
    if not get_settings().orchestrator_api_token:
        logger.warning("orchestrator_api_unprotected reason=ORCHESTRATOR_API_TOKEN_unset")
    asyncio.create_task(_cleanup_sessions_loop())


@app.get("/healthz", response_model=HealthResponse)
async def healthz() -> dict[str, Any]:
    return {"ok": True, "status": "healthy"}


@app.get("/readyz", response_model=ReadyResponse)
async def readyz() -> dict[str, Any]:
    try:
        settings = get_settings()
        conn = get_conn()
        try:
            conn.execute("SELECT 1").fetchone()
        finally:
            conn.close()
    except Exception as exc:
        logger.exception("app_readiness_failed error=%s", exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return {
        "ok": True,
        "status": "ready",
        "db_path": str(settings.computer_use_db_path),
    }


def _session_busy(s: SessionState) -> bool:
    return bool(s.task and not s.task.done())


async def _get_worker_status(s: SessionState) -> dict[str, Any]:
    if not s.worker:
        return {"busy": False, "status": "missing_worker"}

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(f"http://{s.worker.host}:{s.worker.http}/status")
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else {"busy": False, "status": "unknown"}


async def _run_worker_message(session_id: str, s: SessionState, text: str) -> None:
    if not s.worker:
        update_session_status(session_id, "error", "Worker not started", completed=True)
        return

    started_at = time.monotonic()
    failed_logged = False
    try:
        logger.info(
            "message_forward_to_worker session_id=%s worker=%s worker_http=%s text_length=%s",
            session_id,
            s.worker.name,
            s.worker.http,
            len(text),
        )
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"http://{s.worker.host}:{s.worker.http}/messages",
                json={"text": text},
            )
            response.raise_for_status()

        while True:
            try:
                await asyncio.wait_for(
                    s.completion_event.wait(),
                    timeout=WORKER_STATUS_POLL_SECONDS,
                )
                break
            except asyncio.TimeoutError:
                pass

            try:
                status = await _get_worker_status(s)
            except httpx.ReadTimeout:
                logger.info(
                    "Worker status timed out for session %s; continuing to wait for SSE completion",
                    session_id,
                )
                continue

            if status.get("busy"):
                continue

            worker_status = str(status.get("status") or "idle")
            if worker_status == "error":
                logger.error(
                    "task_failed session_id=%s worker=%s duration_seconds=%.3f error=%s",
                    session_id,
                    s.worker.name,
                    time.monotonic() - started_at,
                    str(status.get("error") or "Worker task failed"),
                )
                failed_logged = True
                update_session_status(
                    session_id,
                    "error",
                    str(status.get("error") or "Worker task failed"),
                    completed=True,
                )
            elif worker_status in {"done", "idle"}:
                update_session_status(session_id, "completed", completed=True)
            else:
                update_session_status(session_id, worker_status, completed=True)
            break
    except Exception as exc:
        duration = time.monotonic() - started_at
        logger.exception(
            "task_failed session_id=%s worker=%s duration_seconds=%.3f error=%s",
            session_id,
            s.worker.name,
            duration,
            exc,
        )
        failed_logged = True
        update_session_status(session_id, "error", str(exc), completed=True)
    finally:
        duration = time.monotonic() - started_at
        history = get_session_history(session_id)
        event_count = 0 if not history else len(history["events"])
        status = None if not history else history["session"].get("status")
        if status == "completed":
            logger.info(
                "task_completed session_id=%s worker=%s duration_seconds=%.3f event_count=%s",
                session_id,
                s.worker.name,
                duration,
                event_count,
            )
        elif status == "error" and not failed_logged:
            logger.error(
                "task_failed session_id=%s worker=%s duration_seconds=%.3f event_count=%s error=%s",
                session_id,
                s.worker.name,
                duration,
                event_count,
                None if not history else history["session"].get("error"),
            )
        _touch(s)


def _persist_worker_event(session_id: str, event_name: str, data: dict[str, Any]) -> None:
    try:
        insert_event(session_id, event_name, data)
        logger.info(
            "worker_event_received session_id=%s event=%s data_keys=%s",
            session_id,
            event_name,
            ",".join(sorted(data.keys())),
        )

        if event_name == "assistant_block":
            text = str(data.get("text") or "")
            if text:
                insert_message(session_id, "assistant", text)
        elif event_name == "error":
            update_session_status(
                session_id,
                "error",
                str(data.get("message") or data.get("error") or "Worker error"),
                completed=True,
            )
            session = SESSIONS.get(session_id)
            if session:
                session.completion_event.set()
        elif event_name == "done":
            update_session_status(session_id, "completed", completed=True)
            session = SESSIONS.get(session_id)
            if session:
                session.completion_event.set()
    except Exception:
        logger.exception(
            "Failed to persist worker event %s for session %s",
            event_name,
            session_id,
        )


@app.post(
    "/sessions",
    response_model=SessionCreateResponse,
    dependencies=[Depends(require_orchestrator_token)],
)
async def create_session() -> dict[str, Any]:
    try:
        settings = get_settings()
    except ConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    api_key = settings.anthropic_api_key
    if not api_key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not set in orchestrator")

    session_id = str(uuid.uuid4())
    s = SessionState(session_id=session_id)
    insert_session(session_id)
    logger.info("session_create_requested session_id=%s", session_id)

    try:
        s.worker = start_worker(session_id=session_id, api_key=api_key)
    except RuntimeError as exc:
        logger.exception("Failed to start worker for session %s", session_id)
        _persist_worker_event(
            session_id,
            "error",
            {"message": "Failed to start worker", "stage": "worker_startup"},
        )
        raise HTTPException(status_code=500, detail="Failed to start worker") from exc

    SESSIONS[session_id] = s

    try:
        await _wait_worker_ready(s.worker)
    except WorkerReadyError as exc:
        logger.exception("Worker did not become ready for session %s", session_id)
        stop_worker(s.worker.name)
        SESSIONS.pop(session_id, None)
        _persist_worker_event(
            session_id,
            "error",
            {
                "message": str(exc),
                "stage": "worker_readiness",
                "worker": s.worker.name,
                "http_port": s.worker.http,
                "novnc_port": s.worker.novnc,
            },
        )
        raise HTTPException(status_code=500, detail="Worker did not become ready in time") from exc

    update_session_status(session_id, "ready")
    logger.info(
        "session_created session_id=%s worker=%s host=%s vnc_port=%s novnc_port=%s streamlit_port=%s http_port=%s",
        session_id,
        s.worker.name,
        s.worker.host,
        s.worker.vnc,
        s.worker.novnc,
        s.worker.streamlit,
        s.worker.http,
    )
    return {
        "session_id": session_id,
        "ui_url": f"http://{settings.public_host}:9000/sessions/{session_id}/ui",
        "novnc_url": f"http://{settings.public_host}:{s.worker.novnc}/vnc.html",
        "streamlit_url": None,
        "legacy_streamlit_enabled": False,
        "worker_http": f"http://127.0.0.1:{s.worker.http}",
    }


@app.delete(
    "/sessions/{session_id}",
    response_model=OkResponse,
    dependencies=[Depends(require_orchestrator_token)],
)
async def delete_session(session_id: str) -> dict[str, bool]:
    s = _get_session(session_id)
    logger.info(
        "session_delete_requested session_id=%s busy=%s worker=%s",
        session_id,
        _session_busy(s),
        None if not s.worker else s.worker.name,
    )
    if s.task and not s.task.done():
        s.task.cancel()
    if s.worker:
        stop_worker(s.worker.name)
        _persist_worker_event(
            session_id,
            "deleted",
            {"message": "Session deleted; worker stopped", "worker": s.worker.name},
        )
    SESSIONS.pop(session_id, None)
    update_session_status(session_id, "deleted", completed=True)
    logger.info("session_deleted session_id=%s", session_id)
    return {"ok": True}


@app.get(
    "/sessions/{session_id}",
    response_model=SessionResponse,
    dependencies=[Depends(require_orchestrator_token)],
)
async def get_session(session_id: str) -> dict[str, Any]:
    s = _get_session(session_id)
    history = get_session_history(session_id)
    return {
        "session_id": s.session_id,
        "busy": _session_busy(s),
        "status": None if not history else history["session"].get("status"),
        "worker": None
        if not s.worker
        else {
            "name": s.worker.name,
            "host": s.worker.host,
            "vnc": s.worker.vnc,
            "novnc": s.worker.novnc,
            "streamlit": s.worker.streamlit,
            "http": s.worker.http,
        },
    }


@app.get(
    "/sessions/{session_id}/history",
    dependencies=[Depends(require_orchestrator_token)],
)
async def session_history(session_id: str) -> dict[str, Any]:
    _get_session(session_id)
    history = get_session_history(session_id)
    if history is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return history


@app.get(
    "/sessions/{session_id}/ui",
    response_class=HTMLResponse,
    dependencies=[Depends(require_orchestrator_token)],
)
async def session_ui(session_id: str) -> str:
    s = _get_session(session_id)
    if not s.worker:
        raise HTTPException(status_code=500, detail="Worker not started for session")

    novnc_url = (
        f"http://{get_settings().public_host}:{s.worker.novnc}/vnc.html"
        "?resize=scale&autoconnect=1&view_only=1&reconnect=1&reconnect_delay=2000"
    )

    return f"""<!doctype html>
<html>
<head>
  <title>Computer Use Demo - Session {session_id}</title>
  <meta name="permissions-policy" content="fullscreen=*" />
  <style>
    body {{ margin: 0; padding: 0; overflow: hidden; }}
    iframe {{ width: 100vw; height: 100vh; border: none; }}
  </style>
</head>
<body>
  <iframe src="{novnc_url}" allow="fullscreen"></iframe>
</body>
</html>
"""


@app.get(
    "/sessions/{session_id}/events",
    dependencies=[Depends(require_orchestrator_token)],
)
async def sse_events(session_id: str, request: Request):
    s = _get_session(session_id)
    _touch(s)

    if not s.worker:
        raise HTTPException(status_code=500, detail="Worker not started")

    worker_url = f"http://{s.worker.host}:{s.worker.http}/events"

    async def stream():
        # si el cliente manda Last-Event-ID, lo forwardeamos al worker
        headers: dict[str, str] = {}
        last_id = request.headers.get("Last-Event-ID") or request.headers.get("last-event-id")
        if last_id:
            headers["Last-Event-ID"] = last_id

        retries = 0
        backoff = SSE_RETRY_INITIAL_BACKOFF_SECONDS
        buffer = ""
        event_count = 0
        logger.info(
            "sse_connected session_id=%s worker=%s worker_url=%s last_event_id=%s",
            session_id,
            s.worker.name,
            worker_url,
            last_id,
        )

        try:
            while True:
                try:
                    async with httpx.AsyncClient(timeout=None) as client:
                        async with client.stream("GET", worker_url, headers=headers) as r:
                            r.raise_for_status()
                            retries = 0
                            backoff = SSE_RETRY_INITIAL_BACKOFF_SECONDS

                            async for chunk in r.aiter_text():
                                buffer += chunk

                                while "\n\n" in buffer:
                                    block, buffer = buffer.split("\n\n", 1)
                                    if not block.strip():
                                        continue

                                    event_name, data, event_id = _parse_sse_block(block)

                                    if event_name and data is not None:
                                        event_count += 1
                                        _persist_worker_event(session_id, event_name, data)
                                        _touch(s)

                                    # opcional: si el worker mandó id, lo guardamos como Last-Event-ID para reconectar
                                    if event_id:
                                        headers["Last-Event-ID"] = event_id

                                    yield block + "\n\n"

                            raise httpx.RemoteProtocolError("worker SSE stream ended")

                except (httpx.RemoteProtocolError, httpx.ReadError) as e:
                    retries += 1
                    msg = f"worker SSE stream error after {retries} attempt(s): {e}"
                    logger.warning(
                        "sse_stream_error session_id=%s attempt=%s limit=%s error=%s",
                        session_id,
                        retries,
                        SSE_RETRY_LIMIT,
                        e,
                    )
                    if retries > SSE_RETRY_LIMIT:
                        _persist_worker_event(session_id, "error", {"message": msg})
                        yield f"event: error\ndata: {json.dumps({'message': msg})}\n\n"
                        return
                    await asyncio.sleep(backoff)
                    backoff = min(SSE_RETRY_MAX_BACKOFF_SECONDS, backoff * 2)

                except Exception as e:
                    retries += 1
                    msg = f"Unexpected worker SSE stream error after {retries} attempt(s): {e}"
                    logger.exception(
                        "sse_stream_unexpected_error session_id=%s attempt=%s limit=%s",
                        session_id,
                        retries,
                        SSE_RETRY_LIMIT,
                    )
                    if retries > SSE_RETRY_LIMIT:
                        _persist_worker_event(session_id, "error", {"message": msg})
                        yield f"event: error\ndata: {json.dumps({'message': msg})}\n\n"
                        return
                    await asyncio.sleep(backoff)
                    backoff = min(SSE_RETRY_MAX_BACKOFF_SECONDS, backoff * 2)
        finally:
            logger.info(
                "sse_disconnected session_id=%s worker=%s event_count=%s",
                session_id,
                s.worker.name,
                event_count,
            )

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post(
    "/sessions/{session_id}/messages",
    response_model=MessageAcceptedResponse,
    dependencies=[Depends(require_orchestrator_token)],
)
async def post_message(session_id: str, body: UserMessageIn) -> dict[str, Any]:
    s = _get_session(session_id)
    _touch(s)

    if not s.worker:
        raise HTTPException(status_code=500, detail="Worker not started")

    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    logger.info(
        "message_received session_id=%s text_length=%s busy=%s",
        session_id,
        len(text),
        _session_busy(s),
    )

    async with s.lock:
        if _session_busy(s):
            raise HTTPException(status_code=409, detail="Session is busy")

        insert_message(session_id, "user", text)
        update_session_status(session_id, "running")
        s.completion_event.clear()
        s.task = asyncio.create_task(_run_worker_message(session_id, s, text))

    return {"ok": True, "status": "running"}
