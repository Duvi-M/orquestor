# Claude Computer Use Session Orchestrator

A production-style SaaS-oriented FastAPI orchestration prototype for running
Claude Computer Use as isolated, session-based backend workloads. The system
creates one Docker desktop worker per session, streams agent activity over SSE,
exposes the worker desktop through noVNC, and persists history in SQLite or
PostgreSQL.

This is not a hosted SaaS yet. It is a SaaS-oriented backend architecture
exercise: tenancy, ownership checks, lifecycle limits, protected UI access,
worker-launch abstraction, observability, migrations, and retention foundations
are implemented while preserving a simple local demo.

## Project Status

Current status: production-style SaaS prototype.

- Works locally end to end: browser frontend, FastAPI orchestrator, Docker
  worker, Claude Computer Use, SSE events, noVNC, and persisted history.
- SaaS foundations are implemented: auth/tenancy shape, ownership checks,
  lifecycle limits, protected UI links, launcher boundary, observability,
  PostgreSQL migrations, retention policy, and artifact metadata.
- Not yet implemented: hosted auth, remote worker launcher, object storage,
  deployment hardening, billing, compliance controls, and production admin
  roles.

## Why It Exists

Computer-use agents are expensive, stateful, and operationally risky. They need
more than a chat endpoint: they need worker isolation, session lifecycle
controls, event streaming, desktop access, auditability, retention policy, and
clear security boundaries. This repo keeps those concerns visible without
prematurely adding Kubernetes, queues, billing, or a frontend framework.

## What Works Today

- FastAPI orchestrator with session, message, history, health, readiness,
  metrics, admin, UI-token, and retention endpoints.
- Dependency-free HTML/JS demo frontend.
- One local Docker worker container per session.
- Claude Computer Use execution inside the worker.
- SSE event proxying and persistence.
- noVNC desktop access through ownership-checked orchestrator URLs.
- Local dev auth adapter with users, organizations, memberships, and ownership
  checks.
- Session limits, runtime/idle expiration, message/event quotas, kill switches,
  and worker cleanup.
- Worker lifecycle behind a `WorkerLauncher` protocol with
  `LocalDockerWorkerLauncher`.
- SQLite default persistence plus PostgreSQL/Alembic production path.
- Artifact metadata and retention cleanup foundation.
- Focused tests for auth, limits, UI tokens, launcher behavior, observability,
  database config, migrations, retention, and worker APIs.

## Architecture

```mermaid
flowchart LR
    Browser["Browser frontend<br/>HTML / JS"] -->|"REST"| API["FastAPI orchestrator"]
    Browser -->|"SSE events"| API
    Browser -->|"Open noVNC"| UI["/sessions/{id}/ui<br/>ownership/token checked"]

    API --> Auth["Local auth / tenancy<br/>X-User-Id + X-Org-Id"]
    API --> Limits["Session limits<br/>quotas + lifecycle"]
    API --> DB[("SQLite local<br/>or PostgreSQL")]
    API --> Retention["Retention cleanup<br/>artifact metadata"]
    API --> Launcher["WorkerLauncher protocol"]

    Launcher --> LocalDocker["LocalDockerWorkerLauncher"]
    LocalDocker --> Docker["Docker Engine"]
    Docker --> Worker["One worker container<br/>per session"]

    Worker --> Claude["Claude Computer Use<br/>loop + tools"]
    Worker --> Desktop["Virtual desktop<br/>VNC / noVNC"]
    Worker -->|"SSE"| API
    Worker -->|"screenshots/events"| API

    Retention --> Artifacts["Local artifact storage<br/>future S3/object store"]
    UI --> Desktop
```

Text flow:

```text
Frontend -> FastAPI orchestrator -> WorkerLauncher -> Docker worker
         -> Claude Computer Use -> SSE/noVNC -> SQLite/Postgres history
```

## SaaS Evolution Summary

The repo evolved from a local prototype into a SaaS-shaped architecture in
incremental phases:

- Identity and tenancy: local dev users, organizations, memberships, and
  session ownership checks.
- Session safety: concurrent limits, runtime/idle expiration, message/event
  caps, kill switches, lifecycle statuses, and worker cleanup.
- Protected UI: ownership-checked noVNC access and optional signed temporary UI
  tokens.
- Worker boundary: `WorkerLauncher` protocol with current local Docker
  implementation and future remote launcher slots.
- Observability: request IDs, readiness, metrics, and internal admin visibility.
- Persistence: SQLite local mode plus PostgreSQL support through Alembic
  migrations.
- Retention: soft-deleted sessions, artifact metadata, screenshot file
  foundation, and dry-run retention cleanup.

See [docs/SAAS_EVOLUTION.md](docs/SAAS_EVOLUTION.md) for the phase-by-phase
boundary notes.

## Local Dev Vs Production

Local/dev by default:

- SQLite via `COMPUTER_USE_DB_PATH`
- local dev identity from `X-User-Id` / `X-Org-Id` or `DEV_USER_ID` /
  `DEV_ORG_ID`
- Docker socket access from the FastAPI process
- worker ports bound to localhost
- no hosted auth provider
- no object storage

Production-oriented foundations already present:

- tenant ownership checks
- PostgreSQL-compatible schema and Alembic migrations
- signed UI access tokens
- session quotas and kill switches
- request IDs and operational endpoints
- retention and artifact metadata
- launcher abstraction for future remote worker backends

Still needed for real hosted SaaS:

- OIDC/Auth0/Clerk/Cognito or equivalent hosted auth
- remote/internal worker launcher instead of direct Docker socket access
- object storage for screenshots/artifacts
- production deployment, TLS, ingress, and secrets management
- stronger sandboxing and network egress policy
- billing/cost ledger and provider usage reconciliation

## Quick Start

Python 3.11 is the safest local development version because the worker image
uses Python 3.11. The local API test environment also works on newer Python
versions where dependencies provide wheels.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r computer_use_demo/requirements.txt
python -m pip install -r dev-requirements.txt

export ANTHROPIC_API_KEY="your_anthropic_api_key"
make build-worker
make run-api
```

`computer_use_demo/requirements.txt` is the main runtime dependency file. It
contains FastAPI, Alembic, SQLAlchemy, psycopg, Anthropic SDK dependencies, and
worker/runtime libraries. The root `requirements.txt` is kept as a compatibility
shim that points to the runtime file.

In another terminal:

```bash
make run-web
```

Open:

```text
http://127.0.0.1:5173
```

## Core Commands

```bash
make test           # focused project tests
make build-worker   # build computer-use-demo:local
make run-api        # FastAPI on 127.0.0.1:9000
make run-web        # static frontend on 127.0.0.1:5173
make db-up          # optional local Postgres
make db-migrate     # Alembic upgrade head
make db-down        # stop optional local Postgres
make smoke-local    # health/readiness/frontend smoke check
make clean-workers  # remove project-labeled worker containers
```

`make db-migrate` uses `PYTHON=.venv/bin/python` by default. To use an already
activated environment, run `make PYTHON=python db-migrate`.

## Release/Demo Checklist

```bash
python3 -B -m pytest -q
node --check web/app.js
make build-worker
make db-migrate
make run-api
make run-web
```

Then open `http://127.0.0.1:5173`, create a session, open noVNC, run the Tokyo
weather task, and inspect `/readyz`, `/metrics`, and `/admin/retention`.

## Database Modes

SQLite is the default:

```bash
unset DATABASE_URL
export COMPUTER_USE_DB_PATH="./data/orchestrator.db"
make run-api
```

PostgreSQL is optional:

```bash
make db-up
export DATABASE_URL="postgresql://orchestrator:orchestrator@127.0.0.1:5432/orchestrator"
make db-migrate
make run-api
```

Migrations live in `migrations/versions/`. SQLite `init_db()` remains for simple
local demo setup; Alembic is the production schema path.

## Worker Launcher Model

`WORKER_LAUNCHER=local_docker` is the only implemented launcher. It preserves
the current one-container-per-session execution path, including local port
allocation, labels, readiness checks, CPU/memory/PID limits, SSE URLs, message
URLs, and noVNC metadata.

Future launcher values are documented roadmap placeholders only:
`ecs_fargate`, `fly_machines`, `remote_launcher`, and `kubernetes`.

## Security Boundaries

- Session-scoped endpoints enforce ownership via user/org identity.
- `ORCHESTRATOR_API_TOKEN` optionally protects API endpoints with bearer auth.
- `/sessions/{id}/ui` is ownership-checked; protected UI mode requires signed
  temporary tokens.
- Worker ports are localhost-bound for the local demo.
- Docker socket access is trusted-local only and should be replaced for hosted
  SaaS.
- Secrets, `.env`, DB files, artifacts, logs, and caches are ignored by git.

More detail: [docs/SECURITY_MODEL.md](docs/SECURITY_MODEL.md).

## Observability And Admin

Useful endpoints:

```http
GET /healthz
GET /readyz
GET /metrics
GET /admin/sessions
GET /admin/retention
```

Every HTTP response includes `X-Request-Id`. `LOG_FORMAT=json` enables
single-line JSON logs. Admin endpoints are for trusted local/internal debugging
and must be protected by real admin auth before internet exposure.

## Retention And Artifacts

Session deletion is logical first: deleted sessions receive `deleted_at`, become
hidden from normal ownership checks, and remain available for retention cleanup.

Screenshot events remain inline for frontend compatibility. When screenshot
events include base64 image data, the orchestrator can also write local artifact
files under `ARTIFACT_STORAGE_DIR` and record metadata in the `artifacts` table.
This is a local foundation; production should move bytes to object storage.

`GET /admin/retention` returns a dry-run report. Startup cleanup is disabled by
default with `CLEANUP_RETENTION_ON_STARTUP=false`.

## Environment Reference

Key variables are listed in [.env.example](.env.example). The main groups are:

- API/provider: `ANTHROPIC_API_KEY`, `MODEL`, `TOOL_VERSION`, `MAX_TOKENS`
- local auth: `DEV_USER_ID`, `DEV_ORG_ID`, `ORCHESTRATOR_API_TOKEN`
- persistence: `DATABASE_URL`, `COMPUTER_USE_DB_PATH`
- worker launcher: `WORKER_LAUNCHER`, `WORKER_IMAGE`, `WORKER_CONNECT_HOST`
- UI protection: `PROTECT_SESSION_UI`, `UI_TOKEN_SECRET`,
  `UI_TOKEN_TTL_SECONDS`
- lifecycle limits: `MAX_CONCURRENT_SESSIONS_PER_USER`,
  `MAX_CONCURRENT_SESSIONS_PER_ORG`, `MAX_SESSION_RUNTIME_SECONDS`,
  `MAX_IDLE_SESSION_SECONDS`, `MAX_MESSAGES_PER_SESSION`,
  `MAX_EVENTS_PER_SESSION`, `GLOBAL_KILL_SWITCH`
- retention: `MESSAGE_RETENTION_DAYS`, `EVENT_RETENTION_DAYS`,
  `SCREENSHOT_RETENTION_DAYS`, `WORKER_LOG_RETENTION_DAYS`,
  `DELETED_SESSION_RETENTION_DAYS`, `ARTIFACT_STORAGE_DIR`,
  `CLEANUP_RETENTION_ON_STARTUP`
- observability: `LOG_LEVEL`, `LOG_FORMAT`

## 5-Minute Demo Script

1. Run `make test`.
2. Run `make build-worker`.
3. Start the API: `make run-api`.
4. Start the frontend: `make run-web`.
5. Open `http://127.0.0.1:5173`.
6. Click `Clear local`, then create a session.
7. Click `Open noVNC`.
8. Send a task, for example:

```text
Open a browser, search for the current weather in Tokyo, and tell me the temperature.
```

9. Point out live SSE events: `assistant_block`, `tool_use_start`,
   `tool_result`, `screenshot`, and `done`.
10. Refresh and load `History`.
11. Show `/readyz`, `/metrics`, and `/admin/retention`.
12. Explain SaaS boundaries: local auth adapter, one worker per session,
    PostgreSQL-ready persistence, protected UI tokens, retention metadata, and
    what remains before hosted SaaS.

Longer guide: [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md).

## Testing

```bash
ruff check computer_use_demo tests scripts
python3 -B -m pytest -q
node --check web/app.js
python3 -m py_compile migrations/versions/*.py
```

The default pytest suite does not require a live Postgres server or a real
Anthropic API call.

## Known Limitations

- Not a hosted SaaS deployment.
- No production auth provider or role-based admin authorization yet.
- Local Docker socket access remains a major trust boundary.
- Active worker reattachment after orchestrator restart is incomplete.
- PostgreSQL access is synchronous and minimal; no pooling yet.
- Artifact bytes are local files, not S3/object storage.
- `/metrics` is JSON, not Prometheus format.
- The frontend is a demo console, not a production SaaS UI.

## Roadmap

- Hosted auth and role-aware admin APIs.
- Remote/internal worker launcher.
- Object storage for screenshots and artifacts.
- Stronger worker isolation and network egress policy.
- Cost ledger and billing integration.
- Deployment profile with TLS, secrets management, and observability backend.
- Optional Prometheus/OpenTelemetry metrics/tracing.
- Worker reattachment/reconciliation after orchestrator restart.

## Additional Docs

- [Architecture](docs/ARCHITECTURE.md)
- [SaaS Evolution](docs/SAAS_EVOLUTION.md)
- [Security Model](docs/SECURITY_MODEL.md)
- [Operations](docs/OPERATIONS.md)
- [Demo Script](docs/DEMO_SCRIPT.md)
