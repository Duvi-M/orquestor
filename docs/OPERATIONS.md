# Operations

Operational commands are intentionally local and simple.

## Setup

```bash
make install
export ANTHROPIC_API_KEY="your_anthropic_api_key"
make build-worker
```

## Run Locally

Terminal 1:

```bash
make run-api
```

Terminal 2:

```bash
make run-web
```

Open:

```text
http://127.0.0.1:5173
```

## Health And Readiness

```bash
curl http://127.0.0.1:9000/healthz
curl http://127.0.0.1:9000/readyz
```

`/healthz` confirms the app process is responding. `/readyz` validates runtime
configuration and SQLite connectivity.

## Smoke Check

After `make run-api` and `make run-web` are running:

```bash
make smoke-local
```

The smoke check verifies API health/readiness and the static frontend HTML. It
does not create sessions and does not call the Anthropic API.

## Worker Cleanup

```bash
make clean-workers
```

This removes containers labeled `cambioml=orchestrator`. It does not target
unrelated containers.

Optional startup cleanup:

```bash
export CLEANUP_ORPHAN_WORKERS_ON_STARTUP=true
make run-api
```

## Token-Protected Mode

```bash
export ORCHESTRATOR_API_TOKEN="dev-token"
make run-api
```

Session-scoped endpoints require:

```bash
curl -H "Authorization: Bearer dev-token" \
  http://127.0.0.1:9000/sessions/{id}
```

The static frontend does not inject tokens. Keep the token unset for the
simplest browser demo.

## Useful Logs

Look for these structured messages:

- `app_startup`
- `session_created`
- `worker_container_created`
- `worker_readiness_check_ok`
- `sse_connected`
- `worker_event_received`
- `task_completed`
- `task_failed`
- `session_deleted`

## Troubleshooting

- Worker does not start: check Docker is running and `computer-use-demo:local`
  exists.
- Session create fails readiness: inspect worker logs with `docker logs`.
- noVNC does not open: verify the returned noVNC URL is bound to localhost.
- API returns 401: unset `ORCHESTRATOR_API_TOKEN` for browser demo mode or use
  an API client with `Authorization: Bearer ...`.
