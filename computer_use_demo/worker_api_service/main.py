from __future__ import annotations

import asyncio
import json
from typing import AsyncGenerator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

app = FastAPI(title="Worker API", version="0.1.0")

PING_INTERVAL_SECONDS = 15.0
CHUNK_SIZE = 12


class UserMessageIn(BaseModel):
    text: str


queue: asyncio.Queue[dict] = asyncio.Queue()
_event_id = 1


def _sse_pack(event: str, data: dict, eid: int | None = None) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    id_line = f"id: {eid}\n" if eid is not None else ""
    return f"{id_line}event: {event}\ndata: {payload}\n\n"


async def _emit(event: str, data: dict) -> None:
    global _event_id
    evt = {"id": _event_id, "event": event, "data": data}
    _event_id += 1
    await queue.put(evt)


async def _event_stream(_request: Request) -> AsyncGenerator[str, None]:
    # handshake (también con id)
    await _emit("ready", {"ok": True})

    while True:
        try:
            evt = await asyncio.wait_for(queue.get(), timeout=PING_INTERVAL_SECONDS)
            yield _sse_pack(evt["event"], evt["data"], evt["id"])
        except asyncio.TimeoutError:
            yield _sse_pack("ping", {"ts": asyncio.get_running_loop().time()})


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def root():
    return {"ok": True, "service": "worker_api"}


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
async def messages(body: UserMessageIn):
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    async def run():
        try:
            await _emit("user_message", {"text": text})

            if text.lower() == "crash":
                raise RuntimeError("forced crash")

            out = f"echo: {text}"
            for i in range(0, len(out), CHUNK_SIZE):
                await asyncio.sleep(0.2)
                await _emit("assistant_block", {"type": "text", "text": out[i : i + CHUNK_SIZE]})

            await _emit("done", {"ok": True})
        except Exception as e:
            await _emit("error", {"message": str(e)})

    asyncio.create_task(run())
    return {"ok": True}
