from __future__ import annotations

import logging
import socket
import subprocess
from dataclasses import dataclass

from computer_use_demo.api.config import get_settings

logger = logging.getLogger(__name__)
PROJECT_LABEL = "cambioml=orchestrator"


@dataclass
class WorkerInfo:
    name: str
    host: str
    vnc: int
    novnc: int
    streamlit: int
    http: int


def _get_free_port() -> int:
    """
    Ask OS for an available localhost port.
    Note: there is still a small race window, but good enough for demo/challenge.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _redact_secret(value: str) -> str:
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}...{value[-4:]}"


def _redact_cmd(cmd: list[str], secret_values: list[str]) -> list[str]:
    redacted = cmd[:]
    for secret_value in secret_values:
        if not secret_value:
            continue
        redacted = [
            part.replace(secret_value, _redact_secret(secret_value))
            for part in redacted
        ]
    return redacted


def start_worker(*, session_id: str, api_key: str, image: str | None = None) -> WorkerInfo:
    """
    Starts a dedicated docker container per session, exposing:
      - 5900 VNC
      - 6080 noVNC
      - 8501 Streamlit
      - 8080 (worker HTTP, if your worker runs FastAPI there)
    """
    name = f"cu_{session_id}"

    vnc = _get_free_port()
    streamlit = _get_free_port()
    novnc = _get_free_port()
    http = _get_free_port()
    settings = get_settings()
    worker_image = image or settings.worker_image

    cmd = [
        "docker", "run", "-d",
        "--name", name,
        "--label", PROJECT_LABEL,
        "--label", f"session_id={session_id}",
        "--cpus", str(settings.worker_cpu_limit),
        "--memory", settings.worker_memory_limit,
        "--pids-limit", str(settings.worker_pids_limit),
        "-e", f"ANTHROPIC_API_KEY={api_key}",
        "-e", f"MODEL={settings.model}",
        "-e", f"TOOL_VERSION={settings.tool_version}",
        "-e", f"MAX_TOKENS={settings.max_tokens}",
        "-e", f"ENABLE_STREAMLIT={settings.enable_streamlit}",
        "-e", f"VNC_PASSWORD={settings.vnc_password}",
        "-p", f"127.0.0.1:{vnc}:5900",
        "-p", f"127.0.0.1:{streamlit}:8501",
        "-p", f"127.0.0.1:{novnc}:6080",
        "-p", f"127.0.0.1:{http}:8080",
        worker_image,
    ]

    # IMPORTANT: do NOT use --rm while debugging; if the container exits instantly, you lose logs.
    # If docker run fails, capture_output=True lets you see the exact reason.
    logger.info(
        "worker_container_create_start worker=%s image=%s vnc_port=%s novnc_port=%s streamlit_port=%s http_port=%s cpu_limit=%s memory_limit=%s pids_limit=%s",
        name,
        worker_image,
        vnc,
        novnc,
        streamlit,
        http,
        settings.worker_cpu_limit,
        settings.worker_memory_limit,
        settings.worker_pids_limit,
    )
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        safe_cmd = _redact_cmd(cmd, [api_key, settings.vnc_password])
        raise RuntimeError(
            "Failed to start worker container.\n"
            f"cmd: {' '.join(safe_cmd)}\n"
            f"stdout:\n{proc.stdout}\n"
            f"stderr:\n{proc.stderr}\n"
        )

    logger.info(
        "worker_container_created worker=%s host=%s vnc_port=%s novnc_port=%s streamlit_port=%s http_port=%s",
        name,
        settings.worker_connect_host,
        vnc,
        novnc,
        streamlit,
        http,
    )
    return WorkerInfo(
        name=name,
        host=settings.worker_connect_host,
        vnc=vnc,
        novnc=novnc,
        streamlit=streamlit,
        http=http,
    )


def stop_worker(name: str) -> None:
    # Safe stop (works even if container already exited)
    logger.info("worker_container_stop worker=%s", name)
    subprocess.run(["docker", "rm", "-f", name], check=False)


def list_project_worker_names() -> list[str]:
    proc = subprocess.run(
        ["docker", "ps", "-aq", "--filter", f"label={PROJECT_LABEL}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        logger.warning("Failed to list project worker containers: %s", proc.stderr.strip())
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def cleanup_project_workers() -> int:
    names = list_project_worker_names()
    for name in names:
        stop_worker(name)
    if names:
        logger.info("Cleaned up %s project worker container(s)", len(names))
    return len(names)
