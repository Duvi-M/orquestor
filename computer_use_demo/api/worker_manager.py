from __future__ import annotations

import os
import socket
import subprocess
from dataclasses import dataclass


@dataclass
class WorkerInfo:
    name: str
    vnc: int
    novnc: int
    streamlit: int
    http: int


def _get_free_port() -> int:
    """
    Ask OS for an available localhost port.
    Note: there is still a small race window, but good enough for demo/challenge.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


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

    cmd = [
        "docker", "run", "-d",
        "--name", name,
        "--label", "cambioml=orchestrator",
        "--label", f"session_id={session_id}",
        "-e", f"ANTHROPIC_API_KEY={api_key}",
        "-v", f"{os.path.expanduser('~')}/.anthropic:/home/computeruse/.anthropic",
        "-p", f"127.0.0.1:{vnc}:5900",
        "-p", f"127.0.0.1:{streamlit}:8501",
        "-p", f"127.0.0.1:{novnc}:6080",
        "-p", f"127.0.0.1:{http}:8080",
        image,
    ]

    # IMPORTANT: do NOT use --rm while debugging; if the container exits instantly, you lose logs.
    # If docker run fails, capture_output=True lets you see the exact reason.
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            "Failed to start worker container.\n"
            f"cmd: {' '.join(cmd)}\n"
            f"stdout:\n{proc.stdout}\n"
            f"stderr:\n{proc.stderr}\n"
        )

    return WorkerInfo(name=name, vnc=vnc, novnc=novnc, streamlit=streamlit, http=http)


def stop_worker(name: str) -> None:
    # Safe stop (works even if container already exited)
    subprocess.run(["docker", "rm", "-f", name], check=False)