import pytest

from computer_use_demo.api.config import ConfigError, get_settings


def test_settings_defaults(monkeypatch):
    for name in (
        "ANTHROPIC_API_KEY",
        "ORCHESTRATOR_API_TOKEN",
        "COMPUTER_USE_DB_PATH",
        "PUBLIC_HOST",
        "WORKER_CONNECT_HOST",
        "WORKER_IMAGE",
        "MODEL",
        "TOOL_VERSION",
        "MAX_TOKENS",
        "ENABLE_STREAMLIT",
        "LOG_LEVEL",
        "SESSION_TTL_SECONDS",
        "CLEANUP_EVERY_SECONDS",
        "WORKER_READY_TIMEOUT_SECONDS",
        "WORKER_READY_POLL_SECONDS",
        "WORKER_STATUS_POLL_SECONDS",
        "WORKER_CPU_LIMIT",
        "WORKER_MEMORY_LIMIT",
        "WORKER_PIDS_LIMIT",
        "CLEANUP_ORPHAN_WORKERS_ON_STARTUP",
        "SSE_RETRY_LIMIT",
        "SSE_RETRY_INITIAL_BACKOFF_SECONDS",
        "SSE_RETRY_MAX_BACKOFF_SECONDS",
        "CORS_ALLOWED_ORIGINS",
        "VNC_PASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = get_settings()

    assert settings.public_host == "127.0.0.1"
    assert settings.worker_connect_host == "127.0.0.1"
    assert settings.worker_image == "computer-use-demo:local"
    assert settings.max_tokens == 4096
    assert settings.log_level == "INFO"
    assert settings.worker_cpu_limit == 1.0
    assert settings.worker_memory_limit == "2g"
    assert settings.worker_pids_limit == 512
    assert settings.cleanup_orphan_workers_on_startup is False
    assert settings.sse_retry_limit == 3
    assert settings.cors_allowed_origins == [
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://[::]:5173",
    ]


def test_settings_reads_env(monkeypatch, tmp_path):
    db_path = tmp_path / "orchestrator.db"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-secret")
    monkeypatch.setenv("COMPUTER_USE_DB_PATH", str(db_path))
    monkeypatch.setenv("WORKER_IMAGE", "worker:test")
    monkeypatch.setenv("MAX_TOKENS", "1234")
    monkeypatch.setenv("LOG_LEVEL", "debug")
    monkeypatch.setenv("WORKER_CPU_LIMIT", "1.5")
    monkeypatch.setenv("WORKER_MEMORY_LIMIT", "3g")
    monkeypatch.setenv("WORKER_PIDS_LIMIT", "256")
    monkeypatch.setenv("CLEANUP_ORPHAN_WORKERS_ON_STARTUP", "true")
    monkeypatch.setenv("ORCHESTRATOR_API_TOKEN", "orchestrator-secret")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://one.test,http://two.test")
    monkeypatch.setenv("VNC_PASSWORD", "vnc-secret")

    settings = get_settings()

    assert settings.anthropic_api_key == "test-secret"
    assert settings.orchestrator_api_token == "orchestrator-secret"
    assert settings.computer_use_db_path == db_path
    assert settings.worker_image == "worker:test"
    assert settings.max_tokens == 1234
    assert settings.log_level == "DEBUG"
    assert settings.worker_cpu_limit == 1.5
    assert settings.worker_memory_limit == "3g"
    assert settings.worker_pids_limit == 256
    assert settings.cleanup_orphan_workers_on_startup is True
    assert settings.cors_allowed_origins == ["http://one.test", "http://two.test"]
    assert settings.vnc_password == "vnc-secret"


def test_settings_rejects_invalid_max_tokens(monkeypatch):
    monkeypatch.setenv("MAX_TOKENS", "not-a-number")

    with pytest.raises(ConfigError, match="MAX_TOKENS must be an integer"):
        get_settings()


def test_settings_rejects_non_positive_max_tokens(monkeypatch):
    monkeypatch.setenv("MAX_TOKENS", "0")

    with pytest.raises(ConfigError, match="MAX_TOKENS must be >= 1"):
        get_settings()


def test_settings_repr_does_not_include_api_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "super-secret-api-key")
    monkeypatch.setenv("ORCHESTRATOR_API_TOKEN", "super-secret-token")
    monkeypatch.setenv("VNC_PASSWORD", "super-secret-vnc")

    settings_repr = repr(get_settings())
    assert "super-secret-api-key" not in settings_repr
    assert "super-secret-token" not in settings_repr
    assert "super-secret-vnc" not in settings_repr


def test_settings_rejects_invalid_boolean(monkeypatch):
    monkeypatch.setenv("CLEANUP_ORPHAN_WORKERS_ON_STARTUP", "sometimes")

    with pytest.raises(ConfigError, match="CLEANUP_ORPHAN_WORKERS_ON_STARTUP must be a boolean"):
        get_settings()


def test_settings_rejects_empty_worker_memory(monkeypatch):
    monkeypatch.setenv("WORKER_MEMORY_LIMIT", "")

    with pytest.raises(ConfigError, match="WORKER_MEMORY_LIMIT must not be empty"):
        get_settings()
