# Security

The canonical security notes live in [../SECURITY.md](../SECURITY.md).

In short:

- This is a trusted local-development prototype.
- `ORCHESTRATOR_API_TOKEN` optionally protects session-scoped endpoints.
- `/healthz`, `/readyz`, and `/docs` remain public local diagnostics.
- Worker noVNC/VNC ports are local-first; `VNC_PASSWORD` can be set.
- Docker socket access is powerful and should not be exposed to untrusted users.
- Secrets and runtime data are ignored by git.
