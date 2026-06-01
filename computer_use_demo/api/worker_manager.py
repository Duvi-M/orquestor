from __future__ import annotations

import logging
import os
import socket
import subprocess
from dataclasses import dataclass

logger = logging.getLogger(__name__)


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


def start_worker(*, session_id: str, api_key: str, image: str = "computer-use-demo:local") -> WorkerInfo:
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
    connect_host = os.getenv("WORKER_CONNECT_HOST", "127.0.0.1")

    cmd = [
        "docker", "run", "-d",
        "--name", name,
        "--label", "cambioml=orchestrator",
        "--label", f"session_id={session_id}",
        "-e", f"ANTHROPIC_API_KEY={api_key}",
        "-e", f"MODEL={os.getenv('MODEL', 'claude-sonnet-4-5-20250929')}",
        "-e", f"TOOL_VERSION={os.getenv('TOOL_VERSION', 'computer_use_20250124')}",
        "-e", f"MAX_TOKENS={os.getenv('MAX_TOKENS', '4096')}",
        "-e", f"ENABLE_STREAMLIT={os.getenv('ENABLE_STREAMLIT', 'false')}",
        "-p", f"127.0.0.1:{vnc}:5900",
        "-p", f"127.0.0.1:{streamlit}:8501",
        "-p", f"127.0.0.1:{novnc}:6080",
        "-p", f"127.0.0.1:{http}:8080",
        image,
    ]

    # IMPORTANT: do NOT use --rm while debugging; if the container exits instantly, you lose logs.
    # If docker run fails, capture_output=True lets you see the exact reason.
    logger.info("Starting worker container %s", name)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        safe_cmd = [
            part.replace(api_key, _redact_secret(api_key))
            for part in cmd
        ]
        raise RuntimeError(
            "Failed to start worker container.\n"
            f"cmd: {' '.join(safe_cmd)}\n"
            f"stdout:\n{proc.stdout}\n"
            f"stderr:\n{proc.stderr}\n"
        )

    return WorkerInfo(
        name=name,
        host=connect_host,
        vnc=vnc,
        novnc=novnc,
        streamlit=streamlit,
        http=http,
    )


def stop_worker(name: str) -> None:
    # Safe stop (works even if container already exited)
    logger.info("Stopping worker container %s", name)
    subprocess.run(["docker", "rm", "-f", name], check=False)
