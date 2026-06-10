import pytest

from computer_use_demo.api.config import ConfigError, get_settings


CONFIG_ENV_NAMES = (
    "ANTHROPIC_API_KEY",
    "ORCHESTRATOR_API_TOKEN",
    "DEV_USER_ID",
    "DEV_ORG_ID",
    "MAX_CONCURRENT_SESSIONS_PER_USER",
    "MAX_CONCURRENT_SESSIONS_PER_ORG",
    "MAX_SESSION_RUNTIME_SECONDS",
    "MAX_IDLE_SESSION_SECONDS",
    "MAX_MESSAGES_PER_SESSION",
    "MAX_EVENTS_PER_SESSION",
    "PLATFORM_DISABLE_NEW_SESSIONS",
    "GLOBAL_KILL_SWITCH",
    "ORG_DISABLE_NEW_SESSIONS",
    "PROTECT_SESSION_UI",
    "UI_TOKEN_SECRET",
    "UI_TOKEN_TTL_SECONDS",
    "COMPUTER_USE_DB_PATH",
    "WORKER_LAUNCHER",
    "PUBLIC_HOST",
    "WORKER_CONNECT_HOST",
    "WORKER_IMAGE",
    "MODEL",
    "TOOL_VERSION",
    "MAX_TOKENS",
    "ENABLE_STREAMLIT",
    "LOG_LEVEL",
    "LOG_FORMAT",
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
)


@pytest.fixture(autouse=True)
def isolate_config_env(monkeypatch):
    for name in CONFIG_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_settings_defaults():
    settings = get_settings()

    assert settings.public_host == "127.0.0.1"
    assert settings.worker_connect_host == "127.0.0.1"
    assert settings.worker_image == "computer-use-demo:local"
    assert settings.worker_launcher == "local_docker"
    assert settings.max_tokens == 4096
    assert settings.log_level == "INFO"
    assert settings.log_format == "text"
    assert settings.worker_cpu_limit == 1.0
    assert settings.worker_memory_limit == "2g"
    assert settings.worker_pids_limit == 512
    assert settings.dev_user_id == "dev-user"
    assert settings.dev_org_id == "dev-org"
    assert settings.max_concurrent_sessions_per_user == 10
    assert settings.max_concurrent_sessions_per_org == 50
    assert settings.max_session_runtime_seconds == 3600
    assert settings.max_idle_session_seconds == 1800
    assert settings.max_messages_per_session == 100
    assert settings.max_events_per_session == 5000
    assert settings.platform_disable_new_sessions is False
    assert settings.global_kill_switch is False
    assert settings.org_disable_new_sessions == []
    assert settings.protect_session_ui is False
    assert settings.ui_token_secret == ""
    assert settings.ui_token_ttl_seconds == 300
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
    monkeypatch.setenv("WORKER_LAUNCHER", "local_docker")
    monkeypatch.setenv("WORKER_IMAGE", "worker:test")
    monkeypatch.setenv("MAX_TOKENS", "1234")
    monkeypatch.setenv("LOG_LEVEL", "debug")
    monkeypatch.setenv("LOG_FORMAT", "json")
    monkeypatch.setenv("WORKER_CPU_LIMIT", "1.5")
    monkeypatch.setenv("WORKER_MEMORY_LIMIT", "3g")
    monkeypatch.setenv("WORKER_PIDS_LIMIT", "256")
    monkeypatch.setenv("CLEANUP_ORPHAN_WORKERS_ON_STARTUP", "true")
    monkeypatch.setenv("ORCHESTRATOR_API_TOKEN", "orchestrator-secret")
    monkeypatch.setenv("DEV_USER_ID", "user-env")
    monkeypatch.setenv("DEV_ORG_ID", "org-env")
    monkeypatch.setenv("MAX_CONCURRENT_SESSIONS_PER_USER", "2")
    monkeypatch.setenv("MAX_CONCURRENT_SESSIONS_PER_ORG", "4")
    monkeypatch.setenv("MAX_SESSION_RUNTIME_SECONDS", "600")
    monkeypatch.setenv("MAX_IDLE_SESSION_SECONDS", "300")
    monkeypatch.setenv("MAX_MESSAGES_PER_SESSION", "8")
    monkeypatch.setenv("MAX_EVENTS_PER_SESSION", "16")
    monkeypatch.setenv("PLATFORM_DISABLE_NEW_SESSIONS", "true")
    monkeypatch.setenv("GLOBAL_KILL_SWITCH", "true")
    monkeypatch.setenv("ORG_DISABLE_NEW_SESSIONS", "org-a,org-b")
    monkeypatch.setenv("PROTECT_SESSION_UI", "true")
    monkeypatch.setenv("UI_TOKEN_SECRET", "ui-secret")
    monkeypatch.setenv("UI_TOKEN_TTL_SECONDS", "60")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://one.test,http://two.test")
    monkeypatch.setenv("VNC_PASSWORD", "vnc-secret")

    settings = get_settings()

    assert settings.anthropic_api_key == "test-secret"
    assert settings.orchestrator_api_token == "orchestrator-secret"
    assert settings.dev_user_id == "user-env"
    assert settings.dev_org_id == "org-env"
    assert settings.max_concurrent_sessions_per_user == 2
    assert settings.max_concurrent_sessions_per_org == 4
    assert settings.max_session_runtime_seconds == 600
    assert settings.max_idle_session_seconds == 300
    assert settings.max_messages_per_session == 8
    assert settings.max_events_per_session == 16
    assert settings.platform_disable_new_sessions is True
    assert settings.global_kill_switch is True
    assert settings.org_disable_new_sessions == ["org-a", "org-b"]
    assert settings.protect_session_ui is True
    assert settings.ui_token_secret == "ui-secret"
    assert settings.ui_token_ttl_seconds == 60
    assert settings.computer_use_db_path == db_path
    assert settings.worker_launcher == "local_docker"
    assert settings.worker_image == "worker:test"
    assert settings.max_tokens == 1234
    assert settings.log_level == "DEBUG"
    assert settings.log_format == "json"
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
    monkeypatch.setenv("UI_TOKEN_SECRET", "super-secret-ui")

    settings_repr = repr(get_settings())
    assert "super-secret-api-key" not in settings_repr
    assert "super-secret-token" not in settings_repr
    assert "super-secret-vnc" not in settings_repr
    assert "super-secret-ui" not in settings_repr


def test_settings_rejects_invalid_boolean(monkeypatch):
    monkeypatch.setenv("CLEANUP_ORPHAN_WORKERS_ON_STARTUP", "sometimes")

    with pytest.raises(ConfigError, match="CLEANUP_ORPHAN_WORKERS_ON_STARTUP must be a boolean"):
        get_settings()


def test_settings_rejects_empty_worker_memory(monkeypatch):
    monkeypatch.setenv("WORKER_MEMORY_LIMIT", "")

    with pytest.raises(ConfigError, match="WORKER_MEMORY_LIMIT must not be empty"):
        get_settings()
