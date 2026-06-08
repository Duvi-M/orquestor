from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_DB_PATH = Path("data") / "orchestrator.db"


class ConfigError(ValueError):
    pass


def _str_env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _int_env(name: str, default: int, *, minimum: int = 1) -> int:
    raw = _str_env(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc

    if value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}")
    return value


def _float_env(name: str, default: float, *, minimum: float = 0.0) -> float:
    raw = _str_env(name, str(default))
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number") from exc

    if value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}")
    return value


def _bool_env(name: str, default: bool) -> bool:
    raw = _str_env(name, str(default)).lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} must be a boolean")


def _required_str_env(name: str, default: str) -> str:
    value = _str_env(name, default)
    if not value:
        raise ConfigError(f"{name} must not be empty")
    return value


def _csv_env(name: str, default: str) -> list[str]:
    return [item.strip() for item in _str_env(name, default).split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str = field(default="", repr=False)
    orchestrator_api_token: str = field(default="", repr=False)
    computer_use_db_path: Path = DEFAULT_DB_PATH
    public_host: str = "127.0.0.1"
    worker_connect_host: str = "127.0.0.1"
    worker_image: str = "computer-use-demo:local"
    model: str = "claude-sonnet-4-5-20250929"
    tool_version: str = "computer_use_20250124"
    max_tokens: int = 4096
    enable_streamlit: str = "false"
    log_level: str = "INFO"
    session_ttl_seconds: int = 300
    cleanup_every_seconds: int = 30
    worker_ready_timeout_seconds: float = 25.0
    worker_ready_poll_seconds: float = 0.5
    worker_status_poll_seconds: float = 2.0
    worker_cpu_limit: float = 1.0
    worker_memory_limit: str = "2g"
    worker_pids_limit: int = 512
    cleanup_orphan_workers_on_startup: bool = False
    sse_retry_limit: int = 3
    sse_retry_initial_backoff_seconds: float = 0.25
    sse_retry_max_backoff_seconds: float = 3.0
    cors_allowed_origins: list[str] = field(default_factory=list)
    vnc_password: str = field(default="", repr=False)


def get_settings() -> Settings:
    return Settings(
        anthropic_api_key=_str_env("ANTHROPIC_API_KEY"),
        orchestrator_api_token=_str_env("ORCHESTRATOR_API_TOKEN"),
        computer_use_db_path=Path(_str_env("COMPUTER_USE_DB_PATH", str(DEFAULT_DB_PATH))).expanduser(),
        public_host=_str_env("PUBLIC_HOST", "127.0.0.1"),
        worker_connect_host=_str_env("WORKER_CONNECT_HOST", "127.0.0.1"),
        worker_image=_str_env("WORKER_IMAGE", "computer-use-demo:local"),
        model=_str_env("MODEL", "claude-sonnet-4-5-20250929"),
        tool_version=_str_env("TOOL_VERSION", "computer_use_20250124"),
        max_tokens=_int_env("MAX_TOKENS", 4096),
        enable_streamlit=_str_env("ENABLE_STREAMLIT", "false"),
        log_level=_str_env("LOG_LEVEL", "INFO").upper(),
        session_ttl_seconds=_int_env("SESSION_TTL_SECONDS", 300),
        cleanup_every_seconds=_int_env("CLEANUP_EVERY_SECONDS", 30),
        worker_ready_timeout_seconds=_float_env("WORKER_READY_TIMEOUT_SECONDS", 25.0),
        worker_ready_poll_seconds=_float_env("WORKER_READY_POLL_SECONDS", 0.5),
        worker_status_poll_seconds=_float_env("WORKER_STATUS_POLL_SECONDS", 2.0),
        worker_cpu_limit=_float_env("WORKER_CPU_LIMIT", 1.0),
        worker_memory_limit=_required_str_env("WORKER_MEMORY_LIMIT", "2g"),
        worker_pids_limit=_int_env("WORKER_PIDS_LIMIT", 512),
        cleanup_orphan_workers_on_startup=_bool_env("CLEANUP_ORPHAN_WORKERS_ON_STARTUP", False),
        sse_retry_limit=_int_env("SSE_RETRY_LIMIT", 3, minimum=0),
        sse_retry_initial_backoff_seconds=_float_env("SSE_RETRY_INITIAL_BACKOFF_SECONDS", 0.25),
        sse_retry_max_backoff_seconds=_float_env("SSE_RETRY_MAX_BACKOFF_SECONDS", 3.0),
        cors_allowed_origins=_csv_env(
            "CORS_ALLOWED_ORIGINS",
            "http://127.0.0.1:5173,http://localhost:5173,http://[::]:5173",
        ),
        vnc_password=_str_env("VNC_PASSWORD"),
    )
