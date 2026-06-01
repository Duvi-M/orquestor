# CambioML Claude Computer Use Orchestrator

Backend-focused coding challenge submission for scalable Claude Computer Use
session management.

The project reuses Anthropic's `computer-use-demo` stack (`loop.py`, tools, VNC
desktop image) and replaces Streamlit as the primary user interface with:

```text
Basic HTML/JS frontend
  -> FastAPI orchestrator
  -> isolated Worker FastAPI container per session
  -> Anthropic computer-use sampling loop + tools
  -> SSE progress events
  -> noVNC screen access
```

Streamlit is kept only as legacy/debug code. It is not part of the main product
flow and is disabled by default in the worker container.

## Challenge Requirements Mapping

| Requirement | Implementation |
| --- | --- |
| Reuse Anthropic computer-use-demo | `computer_use_demo/loop.py`, `tools/`, desktop Docker image |
| Replace Streamlit with FastAPI backend | `computer_use_demo/api/main.py` orchestrator + `computer_use_demo/worker_api.py` worker |
| Session creation and management | `POST /sessions`, `GET /sessions/{id}`, `DELETE /sessions/{id}` |
| Real-time progress streaming | `GET /sessions/{id}/events` SSE proxy |
| VNC connection to VM | noVNC URL returned by `POST /sessions` |
| DB persistence for chat history | SQLite sessions, messages, events, status |
| Concurrent sessions | one Docker worker per session, per-session lock for active tasks |
| Docker setup | `Dockerfile`, `Dockerfile.orchestrator`, `docker-compose.yml` |
| Basic frontend | `web/index.html`, `web/app.js`, `web/styles.css` |

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
│   ├── loop.py                  # Anthropic sampling loop
│   ├── streamlit.py             # Legacy/debug UI, not primary flow
│   └── tools/                   # Computer, bash, edit tools
├── demo/
│   └── concurrency_demo.sh
├── image/                       # Worker desktop/noVNC startup scripts
├── tests/
├── web/                         # Primary HTML/JS demo frontend
├── Dockerfile                   # Worker image
├── Dockerfile.orchestrator      # Orchestrator container image
├── docker-compose.yml
├── requirements.txt
├── dev-requirements.txt
└── .env.example
```

## API Overview

### Sessions

```http
POST /sessions
GET /sessions/{session_id}
DELETE /sessions/{session_id}
GET /sessions/{session_id}/history
```

`POST /sessions` creates one isolated Docker worker and returns:

- `session_id`
- `novnc_url`
- `ui_url` for a noVNC-only helper page
- `worker_http` for debugging

### Messages

```http
POST /sessions/{session_id}/messages
Content-Type: application/json

{ "text": "Open a browser and search for today's weather in Tokyo" }
```

Only one active task is allowed per session. A second request to the same session
while a task is running returns `409 Session is busy`. Different sessions can run
at the same time.

### Events

```http
GET /sessions/{session_id}/events
```

SSE event names include:

- `ready`
- `user_message`
- `assistant_block`
- `tool_use_start`
- `tool_result`
- `screenshot`
- `error`
- `done`
- `ping`

The orchestrator forwards worker events and persists them to SQLite.

## Environment Variables

Copy the example file and fill in your API key:

```bash
cp .env.example .env
```

| Variable | Purpose | Default/example |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | Required Claude API key | `your_anthropic_api_key_here` |
| `COMPUTER_USE_DB_PATH` | SQLite runtime DB path | `./data/orchestrator.db` |
| `PUBLIC_HOST` | Host used in URLs returned to the browser | `127.0.0.1` |
| `WORKER_CONNECT_HOST` | Host the orchestrator uses to reach worker ports | `127.0.0.1` locally, `host.docker.internal` in compose |
| `MODEL` | Claude model used by workers | `claude-sonnet-4-5-20250929` |
| `TOOL_VERSION` | Computer Use tool version | `computer_use_20250124` |
| `MAX_TOKENS` | Worker max output tokens | `4096` |
| `ENABLE_STREAMLIT` | Optional legacy/debug Streamlit UI | `false` |

The app does not auto-load `.env`; export variables in your shell or use Docker
Compose's env-file behavior.

## Local Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -r dev-requirements.txt

export ANTHROPIC_API_KEY="your_key_here"
export COMPUTER_USE_DB_PATH="./data/orchestrator.db"
```

Build the worker image:

```bash
docker build -t computer-use-demo:local .
```

Run the orchestrator:

```bash
python -m uvicorn computer_use_demo.api.main:app --host 127.0.0.1 --port 9000
```

Run the frontend:

```bash
python -m http.server 5173 -d web
```

Open:

```text
http://127.0.0.1:5173
```

## Docker Compose Setup

First build the worker image used for per-session containers:

```bash
docker build -t computer-use-demo:local .
```

Then run the orchestrator and frontend:

```bash
export ANTHROPIC_API_KEY="your_key_here"
docker compose up --build
```

Open:

```text
http://127.0.0.1:5173
```

Notes:

- The orchestrator container mounts the Docker socket so it can create isolated
  worker containers.
- Workers still run as separate Docker containers, one per session.
- Workers receive `ANTHROPIC_API_KEY` through their container environment.
- `WORKER_CONNECT_HOST=host.docker.internal` is used in compose so the
  orchestrator container can reach worker ports published on the Docker host.

## Frontend Demo

The frontend under `web/` demonstrates the required product flow:

1. Create one or more sessions.
2. Select a session.
3. Send a task/message to the FastAPI orchestrator.
4. Watch real-time SSE events.
5. Load persisted history after refresh.
6. Open the noVNC link for the worker screen.
7. Run two sessions independently.

The frontend does not talk to workers directly and does not use Streamlit.

## Tests

Focused tests for the challenge backend:

```bash
python -B -m pytest -q tests/test_api_app.py tests/test_db.py tests/test_orchestrator_sessions.py
```

Full suite after installing runtime dependencies:

```bash
pytest
```

If `anthropic` is missing, install `requirements.txt` in the active environment.

## 5-Minute Demo Video Script

1. Show the architecture diagram in this README.
2. Start the orchestrator and frontend.
3. Create Session A and open its noVNC link.
4. Send a task and show SSE events appearing live.
5. Create Session B and send a different task.
6. Show both sessions exist independently in the frontend.
7. Try sending a second task to a busy session and show the `409` behavior.
8. Refresh the browser and click History to show persisted messages/events.
9. Briefly show SQLite DB path and Docker containers.
10. End with known limitations.

## Known Limitations

- Authentication and authorization are intentionally out of scope.
- Session state in the orchestrator is in memory; DB history survives, but active
  worker attachment after orchestrator restart is not implemented.
- Worker replay uses an in-memory event log; persisted events are available
  through `/history`.
- Docker socket access is practical for the challenge, but production would need
  stronger isolation and orchestration.
- SQLite is suitable for the challenge/demo; a distributed deployment should use
  a shared database.
- Streamlit remains in the repo for legacy/debug reference only.
