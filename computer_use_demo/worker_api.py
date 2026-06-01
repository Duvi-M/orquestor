from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from anthropic.types.beta import BetaMessageParam, BetaTextBlockParam
from computer_use_demo.loop import APIProvider, sampling_loop

logger = logging.getLogger(__name__)

# App
app = FastAPI(title="Worker API", version="0.1.0")


# Models
class MessageIn(BaseModel):
    text: str


@dataclass
class WorkerState:
    queue: "asyncio.Queue[dict]" = field(default_factory=asyncio.Queue)
    task: Optional[asyncio.Task] = None
    messages: list[BetaMessageParam] = field(default_factory=list)
    status: str = "idle"
    error: str | None = None

    # SSE replay
    next_event_id: int = 1
    event_log: list[dict] = field(default_factory=list)  # {"id": int, "event": str, "data": dict}


STATE = WorkerState()

EVENT_LOG_MAX = 500
PING_INTERVAL_SECONDS = 15.0


# SSE helpers
def _sse_pack(event: str, data: dict, event_id: int | None = None) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    id_line = f"id: {event_id}\n" if event_id is not None else ""
    return f"{id_line}event: {event}\ndata: {payload}\n\n"


async def _emit(event: str, data: dict) -> None:
    evt = {"id": STATE.next_event_id, "event": event, "data": data}
    STATE.next_event_id += 1
    STATE.event_log.append(evt)

    if len(STATE.event_log) > EVENT_LOG_MAX:
        STATE.event_log = STATE.event_log[-EVENT_LOG_MAX:]

    await STATE.queue.put(evt)


async def _event_stream(request: Request) -> AsyncGenerator[str, None]:
    # handshake (not replayable)
    yield _sse_pack("ready", {"ok": True})

    # replay
    last_event_id = request.headers.get("last-event-id")
    if last_event_id and last_event_id.isdigit():
        last_id = int(last_event_id)
        for evt in STATE.event_log:
            if evt["id"] > last_id:
                yield _sse_pack(evt["event"], evt["data"], evt["id"])
                if evt["event"] in ("done", "error"):
                    return

    # live
    while True:
        try:
            evt = await asyncio.wait_for(STATE.queue.get(), timeout=PING_INTERVAL_SECONDS)
            yield _sse_pack(evt["event"], evt["data"], evt["id"])
        except asyncio.TimeoutError:
            yield _sse_pack("ping", {"ts": asyncio.get_running_loop().time()})


# Minimal “translator” from sampling_loop callbacks -> SSE events
def _emit_content_block(block: Any) -> None:
    """
    sampling_loop output_callback gives dict-like blocks.
    We'll forward a few useful types.
    """
    try:
        if isinstance(block, dict) and block.get("type") == "text":
            # text delta
            text = block.get("text", "")
            asyncio.create_task(_emit("assistant_block", {"type": "text", "text": text}))
            return

        if isinstance(block, dict) and block.get("type") == "tool_use":
            asyncio.create_task(
                _emit(
                    "tool_use_start",
                    {
                        "id": block.get("id"),
                        "name": block.get("name"),
                        "input": block.get("input", {}),
                    },
                )
            )
            return

        # fallback: forward as debug event (optional)
        asyncio.create_task(_emit("debug", {"block": block}))
    except Exception:
        # don't crash worker on serialization errors
        pass


def _emit_tool_result(result: Any, tool_use_id: str) -> None:
    """
    tool_output_callback gives ToolResult + tool_use_id
    We forward a lightweight summary.
    """
    try:
        payload: dict[str, Any] = {"tool_use_id": tool_use_id}
        # ToolResult can contain output/error fields; keep it small
        if hasattr(result, "error") and result.error:
            payload["is_error"] = True
            payload["error"] = str(result.error)
        else:
            payload["is_error"] = False
            # result may contain images; avoid huge payloads by only sending text if present
            if hasattr(result, "output") and result.output:
                payload["output"] = str(result.output)[:4000]
        asyncio.create_task(_emit("tool_result", payload))
        if hasattr(result, "base64_image") and result.base64_image:
            asyncio.create_task(
                _emit(
                    "screenshot",
                    {
                        "tool_use_id": tool_use_id,
                        "image_base64": result.base64_image,
                    },
                )
            )
    except Exception:
        logger.exception("Failed to emit tool result")


# Routes
@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/status")
async def status():
    return {
        "busy": bool(STATE.task and not STATE.task.done()),
        "status": STATE.status,
        "error": STATE.error,
        "messages": len(STATE.messages),
        "events": len(STATE.event_log),
    }


@app.get("/events")
async def events(request: Request):
    return StreamingResponse(
        _event_stream(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/messages")
async def post_message(body: MessageIn):
    if STATE.task and not STATE.task.done():
        raise HTTPException(status_code=409, detail="Worker is busy")

    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not set in worker")

    # If first message, initialize conversation
    if not STATE.messages:
        STATE.messages = []

    STATE.status = "running"
    STATE.error = None

    async def _run():
        try:
            await _emit("user_message", {"text": text})

            # Append user message
            STATE.messages.append(
                {
                    "role": "user",
                    "content": [BetaTextBlockParam(type="text", text=text)],
                }
            )

            # Run real loop
            STATE.messages = await sampling_loop(
                system_prompt_suffix="",
                model=os.getenv("MODEL", "claude-sonnet-4-5-20250929"),
                provider=APIProvider.ANTHROPIC,
                messages=STATE.messages,
                output_callback=_emit_content_block,
                tool_output_callback=_emit_tool_result,
                api_response_callback=lambda *_args, **_kw: None,
                api_key=api_key,
                only_n_most_recent_images=3,
                tool_version=os.getenv("TOOL_VERSION", "computer_use_20250124"),
                max_tokens=int(os.getenv("MAX_TOKENS", "4096")),
                thinking_budget=None,
                token_efficient_tools_beta=False,
            )

            STATE.status = "done"
            await _emit("done", {"ok": True})
        except Exception as e:
            STATE.status = "error"
            STATE.error = str(e)
            logger.exception("Worker task failed")
            await _emit("error", {"message": str(e)})

    STATE.task = asyncio.create_task(_run())
    return {"ok": True, "status": "running"}
