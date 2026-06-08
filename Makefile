.PHONY: install test smoke-local build-worker run-api run-web clean-workers clean-local

install:
	python3 -m venv .venv
	.venv/bin/pip install -r requirements.txt
	.venv/bin/pip install -r dev-requirements.txt

test:
	.venv/bin/python -B -m pytest -q tests/test_api_app.py tests/test_config.py tests/test_db.py tests/test_orchestrator_sessions.py tests/test_worker_api.py tests/test_worker_manager.py

smoke-local:
	.venv/bin/python -B scripts/smoke_local.py

build-worker:
	docker build -t $${WORKER_IMAGE:-computer-use-demo:local} .

run-api:
	.venv/bin/python -m uvicorn computer_use_demo.api.main:app --host 127.0.0.1 --port 9000

run-web:
	.venv/bin/python -m http.server 5173 -d web

clean-workers:
	docker rm -f $$(docker ps -aq --filter label=cambioml=orchestrator) 2>/dev/null || true

clean-local:
	rm -rf .pytest_cache .ruff_cache htmlcov
	rm -f .coverage
