from computer_use_demo.api import worker_manager


class FakeProc:
    def __init__(self, *, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def test_start_worker_applies_resource_limits(monkeypatch):
    commands = []
    ports = iter([5901, 8502, 6083, 8084])

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("WORKER_CPU_LIMIT", "1.5")
    monkeypatch.setenv("WORKER_MEMORY_LIMIT", "3g")
    monkeypatch.setenv("WORKER_PIDS_LIMIT", "256")
    monkeypatch.setenv("WORKER_IMAGE", "worker:test")
    monkeypatch.setenv("VNC_PASSWORD", "vnc-secret")
    monkeypatch.setattr(worker_manager, "_get_free_port", lambda: next(ports))

    def fake_run(cmd, **_kwargs):
        commands.append(cmd)
        return FakeProc()

    monkeypatch.setattr(worker_manager.subprocess, "run", fake_run)

    worker = worker_manager.start_worker(session_id="session-1", api_key="test-key")

    assert worker.name == "cu_session-1"
    cmd = commands[0]
    assert "--cpus" in cmd
    assert cmd[cmd.index("--cpus") + 1] == "1.5"
    assert "--memory" in cmd
    assert cmd[cmd.index("--memory") + 1] == "3g"
    assert "--pids-limit" in cmd
    assert cmd[cmd.index("--pids-limit") + 1] == "256"
    assert "-e" in cmd
    assert "VNC_PASSWORD=vnc-secret" in cmd
    assert "worker:test" == cmd[-1]


def test_start_worker_redacts_api_key_and_vnc_password_on_failure(monkeypatch):
    ports = iter([5901, 8502, 6083, 8084])

    monkeypatch.setenv("VNC_PASSWORD", "vnc-secret-value")
    monkeypatch.setattr(worker_manager, "_get_free_port", lambda: next(ports))

    def fake_run(_cmd, **_kwargs):
        return FakeProc(stderr="bad", returncode=1)

    monkeypatch.setattr(worker_manager.subprocess, "run", fake_run)

    try:
        worker_manager.start_worker(
            session_id="session-1",
            api_key="anthropic-secret-value",
        )
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected RuntimeError")

    assert "anthropic-secret-value" not in message
    assert "vnc-secret-value" not in message


def test_cleanup_project_workers_uses_project_label(monkeypatch):
    commands = []

    def fake_run(cmd, **_kwargs):
        commands.append(cmd)
        if cmd[:3] == ["docker", "ps", "-aq"]:
            return FakeProc(stdout="worker-a\nworker-b\n")
        return FakeProc()

    monkeypatch.setattr(worker_manager.subprocess, "run", fake_run)

    assert worker_manager.cleanup_project_workers() == 2
    assert commands[0] == [
        "docker",
        "ps",
        "-aq",
        "--filter",
        f"label={worker_manager.PROJECT_LABEL}",
    ]
    assert commands[1] == ["docker", "rm", "-f", "worker-a"]
    assert commands[2] == ["docker", "rm", "-f", "worker-b"]
