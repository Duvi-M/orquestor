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
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from computer_use_demo.api.config import ConfigError, get_settings
from computer_use_demo.api.db import (
    count_session_events,
    count_session_messages,
    ensure_identity,
    get_conn,
    get_session_history,
    get_session_owner,
    get_session_record,
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

STATUS_CREATED = "created"
STATUS_STARTING = "starting"
STATUS_READY = "ready"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_STOPPED = "stopped"
STATUS_EXPIRED = "expired"
STATUS_DELETED = "deleted"
STATUS_KILLED = "killed"
LEGACY_STATUS_ERROR = "error"

TERMINAL_SESSION_STATUSES = {
    STATUS_FAILED,
    STATUS_STOPPED,
    STATUS_EXPIRED,
    STATUS_DELETED,
    STATUS_KILLED,
    LEGACY_STATUS_ERROR,
}
MESSAGE_ACCEPTING_STATUSES = {
    STATUS_READY,
    STATUS_COMPLETED,
}


class WorkerReadyError(RuntimeError):
    pass


bearer_scheme = HTTPBearer(auto_error=False)
bearer_dependency = Depends(bearer_scheme)


@dataclass(frozen=True)
class DevIdentity:
    user_id: str
    organization_id: str


def get_default_dev_identity() -> DevIdentity:
    settings = get_settings()
    return DevIdentity(
        user_id=settings.dev_user_id,
        organization_id=settings.dev_org_id,
    )


def get_current_identity(
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
) -> DevIdentity:
    settings = get_settings()
    identity = DevIdentity(
        user_id=(x_user_id or settings.dev_user_id).strip(),
        organization_id=(x_org_id or settings.dev_org_id).strip(),
    )
    if not identity.user_id or not identity.organization_id:
        raise HTTPException(status_code=400, detail="User and organization identity are required")
    ensure_identity(identity.user_id, identity.organization_id)
    return identity


identity_dependency = Depends(get_current_identity)


def _coerce_identity(identity: Any) -> DevIdentity:
    if isinstance(identity, DevIdentity):
        return identity
    default_identity = get_default_dev_identity()
    ensure_identity(default_identity.user_id, default_identity.organization_id)
    return default_identity


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


def _authorize_session(session_id: str, identity: DevIdentity) -> None:
    owner = get_session_owner(session_id)
    if owner is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if (
        owner.get("user_id") != identity.user_id
        or owner.get("organization_id") != identity.organization_id
    ):
        raise HTTPException(status_code=404, detail="Session not found")


def _is_terminal_status(status: str | None) -> bool:
    return status in TERMINAL_SESSION_STATUSES


def _active_session_counts(identity: DevIdentity) -> tuple[int, int]:
    user_count = 0
    org_count = 0
    for session_id in list(SESSIONS):
        owner = get_session_owner(session_id)
        if owner is None:
            continue
        record = get_session_record(session_id)
        status = None if record is None else str(record.get("status") or "")
        if _is_terminal_status(status):
            continue
        if owner.get("user_id") == identity.user_id:
            user_count += 1
        if owner.get("organization_id") == identity.organization_id:
            org_count += 1
    return user_count, org_count


def _reject_if_platform_disabled(identity: DevIdentity) -> None:
    settings = get_settings()
    if settings.global_kill_switch:
        raise HTTPException(status_code=403, detail="Global kill switch is enabled")
    if settings.platform_disable_new_sessions:
        raise HTTPException(status_code=403, detail="New sessions and messages are disabled")
    if identity.organization_id in settings.org_disable_new_sessions:
        raise HTTPException(
            status_code=403,
            detail="New sessions and messages are disabled for this organization",
        )


def _enforce_concurrent_session_limits(identity: DevIdentity) -> None:
    settings = get_settings()
    user_count, org_count = _active_session_counts(identity)
    if user_count >= settings.max_concurrent_sessions_per_user:
        raise HTTPException(
            status_code=429,
            detail=(
                "User concurrent session limit exceeded "
                f"limit={settings.max_concurrent_sessions_per_user}"
            ),
        )
    if org_count >= settings.max_concurrent_sessions_per_org:
        raise HTTPException(
            status_code=429,
            detail=(
                "Organization concurrent session limit exceeded "
                f"limit={settings.max_concurrent_sessions_per_org}"
            ),
        )


def _session_runtime_seconds(s: SessionState, record: dict[str, Any] | None) -> float:
    created_at = None if record is None else record.get("created_at")
    try:
        return time.time() - float(created_at)
    except (TypeError, ValueError):
        return time.time() - s.created_at


def _stop_session_worker(
    session_id: str,
    s: SessionState,
    *,
    status: str,
    event_name: str,
    message: str,
    stop_reason: str,
    remove_from_memory: bool = True,
) -> None:
    if s.task and not s.task.done():
        s.task.cancel()
    if s.worker:
        stop_worker(s.worker.name)
    _persist_worker_event(
        session_id,
        event_name,
        {
            "message": message,
            "reason": stop_reason,
            "worker": None if not s.worker else s.worker.name,
        },
    )
    update_session_status(session_id, status, completed=True, stop_reason=stop_reason)
    if remove_from_memory:
        SESSIONS.pop(session_id, None)


def _expire_session(
    session_id: str,
    s: SessionState,
    *,
    stop_reason: str,
    message: str,
) -> None:
    logger.info(
        "session_expired session_id=%s reason=%s worker=%s",
        session_id,
        stop_reason,
        None if not s.worker else s.worker.name,
    )
    _stop_session_worker(
        session_id,
        s,
        status=STATUS_EXPIRED,
        event_name="session_expired",
        message=message,
        stop_reason=stop_reason,
    )


def _enforce_session_message_limits(session_id: str, s: SessionState) -> None:
    settings = get_settings()
    record = get_session_record(session_id)
    status = None if record is None else str(record.get("status") or "")

    if status == STATUS_EXPIRED:
        raise HTTPException(status_code=429, detail="Session has expired")
    if status == STATUS_KILLED:
        raise HTTPException(status_code=403, detail="Session has been killed")
    if status == STATUS_DELETED:
        raise HTTPException(status_code=409, detail="Session has been deleted")
    if status == STATUS_FAILED:
        raise HTTPException(status_code=409, detail="Session has failed")
    if status and status not in MESSAGE_ACCEPTING_STATUSES and status != STATUS_RUNNING:
        raise HTTPException(status_code=409, detail=f"Session is not ready: {status}")

    if _session_runtime_seconds(s, record) > settings.max_session_runtime_seconds:
        _expire_session(
            session_id,
            s,
            stop_reason="runtime_limit_exceeded",
            message="Session runtime limit exceeded; worker stopped",
        )
        raise HTTPException(status_code=429, detail="Session runtime limit exceeded")

    if count_session_messages(session_id, role="user") >= settings.max_messages_per_session:
        _persist_worker_event(
            session_id,
            "quota_exceeded",
            {
                "limit": settings.max_messages_per_session,
                "quota": "max_messages_per_session",
                "message": "Session message limit exceeded",
            },
        )
        raise HTTPException(status_code=429, detail="Session message limit exceeded")


async def _cleanup_sessions_loop() -> None:
    while True:
        now = time.time()
        expired: list[tuple[str, str, str]] = []
        settings = get_settings()

        for sid, s in list(SESSIONS.items()):
            busy = bool(s.task and not s.task.done())
            runtime_seconds = now - s.created_at
            idle_seconds = now - s.last_activity
            if runtime_seconds > settings.max_session_runtime_seconds:
                expired.append((
                    sid,
                    "runtime_limit_exceeded",
                    "Session runtime limit exceeded; worker stopped",
                ))
            elif (not busy) and idle_seconds > settings.max_idle_session_seconds:
                expired.append((
                    sid,
                    "idle_limit_exceeded",
                    "Session idle limit exceeded; worker stopped",
                ))

        for sid, reason, message in expired:
            s = SESSIONS.get(sid)
            if s:
                logger.info(
                    "session_cleanup_expired session_id=%s worker=%s reason=%s",
                    sid,
                    None if not s.worker else s.worker.name,
                    reason,
                )
                _expire_session(sid, s, stop_reason=reason, message=message)

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
        update_session_status(
            session_id,
            STATUS_FAILED,
            "Worker not started",
            completed=True,
            stop_reason="worker_missing",
        )
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
                    STATUS_FAILED,
                    str(status.get("error") or "Worker task failed"),
                    completed=True,
                    stop_reason="worker_error",
                )
            elif worker_status in {"done", "idle"}:
                update_session_status(session_id, STATUS_COMPLETED, completed=True)
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
        update_session_status(
            session_id,
            STATUS_FAILED,
            str(exc),
            completed=True,
            stop_reason="worker_error",
        )
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
        elif status in {STATUS_FAILED, LEGACY_STATUS_ERROR} and not failed_logged:
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
        settings = get_settings()
        event_count = count_session_events(session_id)
        if event_name != "quota_exceeded" and event_count >= settings.max_events_per_session:
            if event_count == settings.max_events_per_session:
                insert_event(
                    session_id,
                    "quota_exceeded",
                    {
                        "limit": settings.max_events_per_session,
                        "quota": "max_events_per_session",
                        "message": "Session event retention limit exceeded",
                    },
                )
            logger.info(
                "worker_event_skipped_quota_exceeded session_id=%s event=%s limit=%s",
                session_id,
                event_name,
                settings.max_events_per_session,
            )
            return

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
            stage = str(data.get("stage") or "").strip()
            update_session_status(
                session_id,
                STATUS_FAILED,
                str(data.get("message") or data.get("error") or "Worker error"),
                completed=True,
                stop_reason=f"{stage}_failed" if stage else "worker_error",
            )
            session = SESSIONS.get(session_id)
            if session:
                session.completion_event.set()
        elif event_name == "done":
            update_session_status(session_id, STATUS_COMPLETED, completed=True)
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
async def create_session(
    current_identity: DevIdentity = identity_dependency,
) -> dict[str, Any]:
    current_identity = _coerce_identity(current_identity)
    _reject_if_platform_disabled(current_identity)
    _enforce_concurrent_session_limits(current_identity)
    try:
        settings = get_settings()
    except ConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    api_key = settings.anthropic_api_key
    if not api_key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not set in orchestrator")

    session_id = str(uuid.uuid4())
    s = SessionState(session_id=session_id)
    insert_session(
        session_id,
        user_id=current_identity.user_id,
        organization_id=current_identity.organization_id,
    )
    update_session_status(session_id, STATUS_STARTING)
    logger.info(
        "session_create_requested session_id=%s user_id=%s organization_id=%s",
        session_id,
        current_identity.user_id,
        current_identity.organization_id,
    )

    try:
        s.worker = start_worker(session_id=session_id, api_key=api_key)
    except RuntimeError as exc:
        logger.exception("Failed to start worker for session %s", session_id)
        _persist_worker_event(
            session_id,
            "error",
            {"message": "Failed to start worker", "stage": "worker_startup"},
        )
        update_session_status(
            session_id,
            STATUS_FAILED,
            "Failed to start worker",
            completed=True,
            stop_reason="worker_startup_failed",
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
        update_session_status(
            session_id,
            STATUS_FAILED,
            "Worker did not become ready in time",
            completed=True,
            stop_reason="worker_readiness_failed",
        )
        raise HTTPException(status_code=500, detail="Worker did not become ready in time") from exc

    update_session_status(session_id, STATUS_READY)
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
async def delete_session(
    session_id: str,
    current_identity: DevIdentity = identity_dependency,
) -> dict[str, bool]:
    current_identity = _coerce_identity(current_identity)
    _authorize_session(session_id, current_identity)
    s = _get_session(session_id)
    logger.info(
        "session_delete_requested session_id=%s busy=%s worker=%s",
        session_id,
        _session_busy(s),
        None if not s.worker else s.worker.name,
    )
    if s.task and not s.task.done():
        s.task.cancel()
    _stop_session_worker(
        session_id,
        s,
        status=STATUS_DELETED,
        event_name="session_deleted",
        message="Session deleted; worker stopped",
        stop_reason="user_deleted",
    )
    logger.info("session_deleted session_id=%s", session_id)
    return {"ok": True}


@app.get(
    "/sessions/{session_id}",
    response_model=SessionResponse,
    dependencies=[Depends(require_orchestrator_token)],
)
async def get_session(
    session_id: str,
    current_identity: DevIdentity = identity_dependency,
) -> dict[str, Any]:
    current_identity = _coerce_identity(current_identity)
    _authorize_session(session_id, current_identity)
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
async def session_history(
    session_id: str,
    current_identity: DevIdentity = identity_dependency,
) -> dict[str, Any]:
    current_identity = _coerce_identity(current_identity)
    _authorize_session(session_id, current_identity)
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
async def session_ui(
    session_id: str,
    current_identity: DevIdentity = identity_dependency,
) -> str:
    current_identity = _coerce_identity(current_identity)
    _authorize_session(session_id, current_identity)
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
async def sse_events(
    session_id: str,
    request: Request,
    current_identity: DevIdentity = identity_dependency,
):
    current_identity = _coerce_identity(current_identity)
    _authorize_session(session_id, current_identity)
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
async def post_message(
    session_id: str,
    body: UserMessageIn,
    current_identity: DevIdentity = identity_dependency,
) -> dict[str, Any]:
    current_identity = _coerce_identity(current_identity)
    _authorize_session(session_id, current_identity)
    _reject_if_platform_disabled(current_identity)
    s = _get_session(session_id)

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
        _enforce_session_message_limits(session_id, s)

        if _session_busy(s):
            raise HTTPException(status_code=409, detail="Session is busy")

        if not s.worker:
            raise HTTPException(status_code=500, detail="Worker not started")

        _touch(s)
        insert_message(session_id, "user", text)
        update_session_status(session_id, STATUS_RUNNING)
        s.completion_event.clear()
        s.task = asyncio.create_task(_run_worker_message(session_id, s, text))

    return {"ok": True, "status": "running"}
