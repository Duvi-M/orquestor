# Demo Guide

This is a five-minute demo flow for portfolio reviews and interviews.

## Before The Demo

```bash
make test
export ANTHROPIC_API_KEY="your_anthropic_api_key"
make build-worker
```

Start services:

```bash
make run-api
make run-web
```

Open:

```text
http://127.0.0.1:5173
```

## Talk Track

1. This project treats computer-use agents as backend workloads.
2. A FastAPI orchestrator owns sessions, workers, SSE, noVNC URLs, and history.
3. Each session gets its own Dockerized desktop worker.
4. The worker runs Claude Computer Use and emits structured events.
5. SQLite keeps enough history for replay/debugging.
6. Local security controls are explicit: optional bearer token, localhost
   bindings, VNC password support, Docker label cleanup, and documented Docker
   socket risk.

## Live Steps

1. Create Session A.
2. Open noVNC.
3. Send:

```text
Open Firefox and search for the current weather in Tokyo.
```

4. Point out timeline events:
   - `ASSISTANT_BLOCK`
   - `TOOL_USE_START`
   - `TOOL_RESULT`
   - `SCREENSHOT`
   - `DONE`
5. Create Session B and send a different task.
6. Show two sessions have separate workers/ports.
7. Refresh the frontend and click `History`.
8. Delete one session and point out worker cleanup logs.

## Screenshot/GIF Checklist

Capture:

- frontend with active session and event timeline
- noVNC desktop during a task
- terminal logs showing session and worker lifecycle
- optional two-session demo GIF

Avoid capturing:

- API keys
- `.env` files
- private browser tabs
- private challenge text
- reviewer or organization names
