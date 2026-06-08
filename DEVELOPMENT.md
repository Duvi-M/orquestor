# Development

## Local Setup

```bash
make install
```

## Test And Lint

```bash
make test
.venv/bin/ruff check computer_use_demo tests scripts
```

The focused test suite mocks worker behavior and does not call the real
Anthropic API.

## Run The App

```bash
export ANTHROPIC_API_KEY="your_anthropic_api_key"
make build-worker
make run-api
make run-web
```

## Coding Guidelines

- Preserve the one-worker-per-session architecture.
- Keep the static frontend dependency-free.
- Keep security claims honest: local production-style prototype, not hardened
  SaaS.
- Add tests for failure paths when touching lifecycle, SSE, config, or worker
  management.
- Do not commit `.env`, logs, databases, screenshots with secrets, or worker
  runtime artifacts.
