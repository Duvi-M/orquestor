# Demo Script

This is a five-minute walkthrough for interviews, portfolio reviews, or local
architecture demos.

## Before The Demo

```bash
make test
node --check web/app.js
export ANTHROPIC_API_KEY="your_anthropic_api_key"
make build-worker
make db-migrate
```

Start services:

```bash
make run-api
make run-web
```

Open `http://127.0.0.1:5173`.

## Live Flow

1. Click `Clear local`.
2. Create a new session.
3. Click `Open noVNC`.
4. Send a task:

```text
Open a browser, search for the current weather in Tokyo, and tell me the temperature.
```

5. Narrate the timeline:
   - `assistant_block`: model narration
   - `tool_use_start`: computer/tool action begins
   - `tool_result`: action result
   - `screenshot`: desktop observation
   - `done`: task completion
6. Refresh the page and load `History`.
7. Open:

```text
http://127.0.0.1:9000/readyz
http://127.0.0.1:9000/metrics
http://127.0.0.1:9000/admin/retention
```

8. Delete the session and point out worker cleanup logs.

## Talk Track

- The product concept is SaaS-style orchestration for computer-use agent
  sessions.
- Each session has one isolated Docker desktop worker.
- The FastAPI orchestrator owns tenancy, lifecycle limits, worker launch,
  protected UI access, SSE persistence, and retention metadata.
- SQLite keeps the local demo simple; PostgreSQL/Alembic is the production data
  path.
- The worker launcher boundary is ready for a future remote launcher.
- The current repo is production-style, not production-hosted.

## SaaS Boundaries To Call Out

- Local dev auth now models users and organizations but is not hosted auth.
- noVNC is protected through the orchestrator but still local-first.
- Retention is safe by default and reports dry-run cleanup.
- Artifacts are local files today; object storage is a future production step.
- Docker socket access is the biggest local trust boundary.

## Capture Checklist

Suggested assets if you want portfolio screenshots later:

- frontend with live event timeline
- noVNC desktop during a task
- terminal logs showing worker lifecycle
- `/metrics` or `/readyz` output

Avoid capturing API keys, `.env` files, personal browser tabs, private prompts,
or reviewer/customer information.
