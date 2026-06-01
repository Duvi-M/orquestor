from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from computer_use_demo.api.db import (
    get_session_history,
    init_db,
    insert_event,
    insert_message,
    insert_session,
    update_session_activity,
    update_session_status,
)
from computer_use_demo.api.worker_manager import WorkerInfo, start_worker, stop_worker

logger = logging.getLogger(__name__)

app = FastAPI(title="Computer Use Backend (Challenge)", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://[::]:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SESSION_TTL_SECONDS = 300
CLEANUP_EVERY_SECONDS = 30

WORKER_READY_TIMEOUT_SECONDS = 25.0
WORKER_READY_POLL_SECONDS = 0.5
WORKER_STATUS_POLL_SECONDS = 2.0
PUBLIC_HOST = os.getenv("PUBLIC_HOST", "127.0.0.1")


class UserMessageIn(BaseModel):
    text: str


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
                logger.info("Cleaning up expired session %s", sid)
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


async def _wait_worker_ready(worker_host: str, worker_http_port: int) -> None:
    url = f"http://{worker_host}:{worker_http_port}/health"
    deadline = time.time() + WORKER_READY_TIMEOUT_SECONDS

    async with httpx.AsyncClient(timeout=2.0) as client:
        while time.time() < deadline:
            try:
                r = await client.get(url)
                if r.status_code == 200:
                    return
            except Exception:
                pass
            await asyncio.sleep(WORKER_READY_POLL_SECONDS)

    raise HTTPException(status_code=500, detail="Worker did not become ready in time")


@app.on_event("startup")
async def startup() -> None:
    init_db()
    logger.info("Orchestrator started")
    asyncio.create_task(_cleanup_sessions_loop())


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

    try:
        logger.info("Orchestrator forwarding message for session %s", session_id)
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
        logger.exception("Worker message failed for session %s", session_id)
        update_session_status(session_id, "error", str(exc), completed=True)
    finally:
        _touch(s)


def _persist_worker_event(session_id: str, event_name: str, data: dict[str, Any]) -> None:
    try:
        insert_event(session_id, event_name, data)

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


@app.post("/sessions")
async def create_session() -> dict[str, Any]:
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not set in orchestrator")

    session_id = str(uuid.uuid4())
    s = SessionState(session_id=session_id)

    try:
        s.worker = start_worker(session_id=session_id, api_key=api_key)
    except RuntimeError as exc:
        logger.exception("Failed to start worker for session %s", session_id)
        raise HTTPException(status_code=500, detail="Failed to start worker") from exc

    SESSIONS[session_id] = s
    insert_session(session_id)

    try:
        await _wait_worker_ready(s.worker.host, s.worker.http)
    except HTTPException:
        logger.exception("Worker did not become ready for session %s", session_id)
        stop_worker(s.worker.name)
        SESSIONS.pop(session_id, None)
        update_session_status(session_id, "error", "Worker did not become ready", completed=True)
        raise

    update_session_status(session_id, "ready")
    logger.info("Created session %s with worker %s", session_id, s.worker.name)
    return {
        "session_id": session_id,
        "ui_url": f"http://{PUBLIC_HOST}:9000/sessions/{session_id}/ui",
        "novnc_url": f"http://{PUBLIC_HOST}:{s.worker.novnc}/vnc.html",
        "streamlit_url": None,
        "legacy_streamlit_enabled": False,
        "worker_http": f"http://127.0.0.1:{s.worker.http}",
    }


@app.delete("/sessions/{session_id}")
async def delete_session(session_id: str) -> dict[str, Any]:
    s = _get_session(session_id)
    if s.worker:
        stop_worker(s.worker.name)
    SESSIONS.pop(session_id, None)
    update_session_status(session_id, "deleted", completed=True)
    logger.info("Deleted session %s", session_id)
    return {"ok": True}


@app.get("/sessions/{session_id}")
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


@app.get("/sessions/{session_id}/history")
async def session_history(session_id: str) -> dict[str, Any]:
    _get_session(session_id)
    history = get_session_history(session_id)
    if history is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return history


@app.get("/sessions/{session_id}/ui", response_class=HTMLResponse)
async def session_ui(session_id: str) -> str:
    s = _get_session(session_id)
    if not s.worker:
        raise HTTPException(status_code=500, detail="Worker not started for session")

    novnc_url = (
        f"http://{PUBLIC_HOST}:{s.worker.novnc}/vnc.html"
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


@app.get("/sessions/{session_id}/events")
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

        backoff = 0.25
        max_backoff = 3.0
        buffer = ""

        while True:
            try:
                async with httpx.AsyncClient(timeout=None) as client:
                    async with client.stream("GET", worker_url, headers=headers) as r:
                        r.raise_for_status()
                        backoff = 0.25

                        async for chunk in r.aiter_text():
                            buffer += chunk

                            while "\n\n" in buffer:
                                block, buffer = buffer.split("\n\n", 1)
                                if not block.strip():
                                    continue

                                event_name, data, event_id = _parse_sse_block(block)

                                if event_name and data is not None:
                                    _persist_worker_event(session_id, event_name, data)
                                    _touch(s)

                                # opcional: si el worker mandó id, lo guardamos como Last-Event-ID para reconectar
                                if event_id:
                                    headers["Last-Event-ID"] = event_id

                                yield block + "\n\n"

            except (httpx.RemoteProtocolError, httpx.ReadError) as e:
                msg = f"worker SSE stream error: {e}"
                logger.warning("SSE stream error for session %s: %s", session_id, e)
                _persist_worker_event(session_id, "error", {"message": msg})
                yield f"event: error\ndata: {json.dumps({'message': msg})}\n\n"
                await asyncio.sleep(backoff)
                backoff = min(max_backoff, backoff * 2)

            except Exception as e:
                msg = str(e)
                logger.exception("Unexpected SSE stream error for session %s", session_id)
                _persist_worker_event(session_id, "error", {"message": msg})
                yield f"event: error\ndata: {json.dumps({'message': msg})}\n\n"
                await asyncio.sleep(backoff)
                backoff = min(max_backoff, backoff * 2)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/sessions/{session_id}/messages")
async def post_message(session_id: str, body: UserMessageIn) -> dict[str, Any]:
    s = _get_session(session_id)
    _touch(s)

    if not s.worker:
        raise HTTPException(status_code=500, detail="Worker not started")

    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    logger.info("Orchestrator received user message for session %s", session_id)

    async with s.lock:
        if _session_busy(s):
            raise HTTPException(status_code=409, detail="Session is busy")

        insert_message(session_id, "user", text)
        update_session_status(session_id, "running")
        s.completion_event.clear()
        s.task = asyncio.create_task(_run_worker_message(session_id, s, text))

    return {"ok": True, "status": "running"}
