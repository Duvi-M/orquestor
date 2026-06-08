# Security Notes

This project is a local, trusted-development prototype for orchestrating Claude
Computer Use workers. It is not designed to be exposed directly to the public
internet.

## Trust Boundary

The intended default deployment is:

- developer machine or trusted local network
- API bound to localhost or a private interface
- frontend served locally
- worker noVNC ports bound to `127.0.0.1`
- Docker socket access available only to the trusted orchestrator process

## Optional API Token

Set `ORCHESTRATOR_API_TOKEN` to require:

```http
Authorization: Bearer <token>
```

for session-scoped orchestrator endpoints. When the token is unset, the API is
open for local development and logs a warning at startup.

`/healthz`, `/readyz`, and FastAPI docs are public to keep local diagnostics
simple. Do not expose them on an untrusted network.

## Docker Socket Risk

The orchestrator mounts or accesses the Docker socket so it can create one
worker container per session. Docker socket access is effectively host-level
control. A compromised orchestrator process can create privileged workloads,
inspect containers, and affect the host.

This is acceptable for trusted local development. Future hardening options
include:

- a narrow worker-launch sidecar instead of raw socket access
- Docker API proxy with allowlisted operations
- rootless Docker or a dedicated VM
- a remote sandbox service
- Kubernetes or another scheduler for isolated environments

## noVNC/VNC

Worker VNC/noVNC ports are bound to localhost by default. `VNC_PASSWORD` can be
set to require a password in the worker's VNC server. If unset, VNC remains
passwordless for the simplest local demo flow.

## Secrets

Do not commit `.env` files, database files, logs, caches, or real API keys. The
repository `.gitignore` excludes those local artifacts. Runtime secrets are
read from environment variables and should not be logged or exposed to the
static frontend.
