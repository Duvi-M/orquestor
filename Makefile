.PHONY: install test test-project test-legacy test-all smoke-local db-up db-migrate db-down build-worker run-api run-web clean-workers clean-local

PROJECT_TESTS := tests/test_api_app.py tests/test_config.py tests/test_db.py tests/test_migrations.py tests/test_orchestrator_sessions.py tests/test_worker_api.py tests/test_worker_manager.py tests/test_worker_launcher.py
LEGACY_TESTS := tests/loop_test.py tests/streamlit_test.py tests/tools
PYTHON ?= .venv/bin/python

install:
	python3 -m venv .venv
	.venv/bin/pip install -r computer_use_demo/requirements.txt
	.venv/bin/pip install -r dev-requirements.txt

test:
	.venv/bin/python -B -m pytest -q

test-project:
	.venv/bin/python -B -m pytest -q $(PROJECT_TESTS)

test-legacy:
	@echo "Legacy Anthropic Computer Use tests require optional upstream dependencies such as anthropic and streamlit."
	.venv/bin/python -B -m pytest -q -o "python_files=test_*.py *_test.py" $(LEGACY_TESTS)

test-all:
	$(MAKE) test-project
	$(MAKE) test-legacy

smoke-local:
	.venv/bin/python -B scripts/smoke_local.py

db-up:
	docker compose --profile postgres up -d postgres

db-migrate:
	$(PYTHON) -m alembic upgrade head

db-down:
	docker compose --profile postgres stop postgres

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
