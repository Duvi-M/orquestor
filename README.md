# Claude Computer Use Session Orchestrator

A FastAPI-based orchestration backend for running isolated Claude Computer Use
agent sessions with real-time events, noVNC screen access, Dockerized workers,
and persistent session history.

This is a personal engineering project exploring how Operator-style
computer-use agents can be managed through a backend architecture instead of a
single-user Streamlit demo.

## Motivation

Computer-use agents are most useful when they can be started, isolated,
observed, and stopped like real backend workloads. This project takes
Anthropic's Computer Use demo stack and wraps it with a session-oriented
orchestrator:

- one isolated desktop worker per session
- FastAPI APIs for session lifecycle and task submission
- Server-Sent Events for real-time progress
- noVNC for observing the virtual desktop
- SQLite persistence for session history and debugging

It is a working prototype with production-style boundaries, not a hardened
production platform.

## Architecture

```text
HTML/JS Frontend
    ↓
FastAPI Orchestrator
    ↓
Per-session Docker Worker
    ↓
Anthropic Computer Use loop/tools
    ↓
SSE events + noVNC screen access
```

The primary interface is the dependency-free frontend under `web/`. Streamlit is
kept only as legacy/debug reference code and is not part of the main flow.

## Features

- FastAPI orchestrator backend
- Session lifecycle APIs
- One isolated worker container per session
- Real-time Server-Sent Events streaming
- noVNC desktop access for each worker
- SQLite persistence for sessions, messages, status, errors, and events
- Basic HTML/CSS/JavaScript frontend
- Docker-based local workflow
- Focused backend tests with mocked worker behavior
- Anthropic Computer Use loop/tools reused instead of reimplemented
- Streamlit retained only as legacy/debug code

## Tech Stack

- Python
- FastAPI
- Docker
- SQLite
- Server-Sent Events
- VNC / noVNC
- Anthropic Claude Computer Use stack
- HTML, CSS, JavaScript

## Repository Structure

```text
.
├── computer_use_demo/
│   ├── api/
│   │   ├── main.py              # FastAPI orchestrator
│   │   ├── db.py                # SQLite persistence helpers
│   │   └── worker_manager.py    # Docker worker lifecycle
│   ├── worker_api.py            # Primary Worker FastAPI API
│   ├── worker_api_service/      # Lightweight echo/SSE stub for experiments
│   ├── loop.py                  # Anthropic Computer Use sampling loop
│   ├── streamlit.py             # Legacy/debug UI, not primary flow
│   └── tools/                   # Computer, bash, and edit tools
├── demo/
│   └── concurrency_demo.sh
├── image/                       # Worker desktop/noVNC startup scripts
├── tests/
├── web/                         # Primary HTML/JS frontend
├── Dockerfile                   # Worker image
├── Dockerfile.orchestrator      # Orchestrator container image
├── docker-compose.yml
├── requirements.txt
├── dev-requirements.txt
├── ruff.toml
├── pyproject.toml
└── .env.example
```

## Setup

Create a Python environment and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
pip install -r dev-requirements.txt
```

Export the required environment variables:

```bash
export ANTHROPIC_API_KEY="your_anthropic_api_key"
export COMPUTER_USE_DB_PATH="./data/orchestrator.db"
export PUBLIC_HOST="127.0.0.1"
export WORKER_CONNECT_HOST="127.0.0.1"
export MODEL="claude-sonnet-4-5-20250929"
export TOOL_VERSION="computer_use_20250124"
export MAX_TOKENS="4096"
export ENABLE_STREAMLIT="false"
```

Build the worker image:

```bash
docker build -t computer-use-demo:local .
```

Start the orchestrator:

```bash
python -m uvicorn computer_use_demo.api.main:app --host 127.0.0.1 --port 9000
```

Start the frontend in another terminal:

```bash
python -m http.server 5173 -d web
```

Open:

```text
http://127.0.0.1:5173
```

## Docker Compose

The compose setup runs the orchestrator and static frontend. Worker containers
are still created dynamically by the orchestrator, one per session.

```bash
export ANTHROPIC_API_KEY="your_anthropic_api_key"
docker build -t computer-use-demo:local .
docker compose up --build
```

Open:

```text
http://127.0.0.1:5173
```

The orchestrator container mounts the Docker socket so it can create worker
containers. This is practical for local development, but it should be hardened
before use in untrusted environments.

## Usage

1. Open the frontend.
2. Click `New Session`.
3. Select the session in the sidebar.
4. Click `Open noVNC` to observe the worker desktop.
5. Send a task such as:

```text
Open Firefox and search for the current weather in Tokyo.
```

6. Watch real-time events in the frontend:
   - `user_message`
   - `assistant_block`
   - `tool_use_start`
   - `tool_result`
   - `screenshot`
   - `done`
   - `error`
7. Refresh the browser and click `History` to reload persisted events.
8. Create a second session to verify independent worker containers.

The frontend stores recent session IDs in local browser storage. Use `Clear local`
if the backend has been restarted and old sessions no longer exist.

## API Overview

```http
POST   /sessions
GET    /sessions/{id}
DELETE /sessions/{id}

POST   /sessions/{id}/messages
GET    /sessions/{id}/events
GET    /sessions/{id}/history
```

`POST /sessions` returns:

- `session_id`
- `novnc_url`
- `ui_url`
- `worker_http`

`GET /sessions/{id}/events` streams worker events through SSE.

`GET /sessions/{id}/history` returns persisted session metadata, messages, and
events from SQLite.

## Testing

Focused backend tests:

```bash
python -B -m pytest -q tests/test_api_app.py tests/test_db.py tests/test_orchestrator_sessions.py tests/test_worker_api.py
```

Full suite:

```bash
pytest
```

The worker tests use mocks and do not call the real Anthropic API.

## Demo Script

A concise five-minute demo:

1. Show the architecture diagram.
2. Start Docker, the orchestrator, and the frontend.
3. Create Session A.
4. Open noVNC for Session A.
5. Send a browser/search task.
6. Show live SSE events and desktop activity.
7. Create Session B and send a different task.
8. Show both sessions have independent containers.
9. Refresh the frontend and load History.
10. Show focused tests passing.

## Known Limitations

- SQLite is used for local/demo persistence.
- Active worker reattachment after orchestrator restart is not fully implemented.
- Docker socket mounting is convenient locally but requires hardening in
  production-like deployments.
- Real Claude execution requires a valid Anthropic API key.
- The frontend is intentionally basic and optimized for demonstrating APIs.
- Authentication and authorization are not included.

## Future Improvements

- PostgreSQL persistence
- Worker reattachment and recovery after orchestrator restart
- Authentication and per-user authorization
- Queue-based scheduling for tasks and workers
- Kubernetes deployment model
- Richer frontend with screenshots and task timelines
- WebSocket streaming alternative
- Structured observability with metrics and tracing

## Security Notes

- Never commit `.env` or real API keys.
- Do not expose the Docker socket in untrusted environments.
- API keys are passed through environment variables to worker containers.
- noVNC ports are bound locally by default; review network exposure before
  deploying remotely.
