"""Multi-process Supervisor Manager coordinating API, Engine, and Port Allocation (§16.1, §16.2)."""

from __future__ import annotations

import contextlib
import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from quantdesk.supervisor.heartbeat import HeartbeatMonitor, SupervisedRecoveryWatchdog
from quantdesk.supervisor.lock import AccountLock

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


@dataclass(frozen=True)
class ActiveInstanceInfo:
    """Information regarding a currently running QuantDesk instance."""

    pid: int
    api_port: int
    base_url: str
    account: str
    mode: str
    epoch: int
    started_at_ns: int


def is_port_in_use(port: int, host: str = DEFAULT_HOST) -> bool:
    """Checks whether a TCP port is currently occupied on loopback."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.2)
        try:
            return s.connect_ex((host, port)) == 0
        except OSError:
            return False


def find_free_loopback_port(preferred: int = DEFAULT_PORT, host: str = DEFAULT_HOST) -> int:
    """Returns preferred port if free, otherwise allocates an available ephemeral loopback port."""
    if not is_port_in_use(preferred, host):
        return preferred
    # Bind to port 0 to let OS assign an available port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        _, port = s.getsockname()
        return int(port)


def verify_running_quantdesk_instance(
    base_url: str, timeout_sec: float = 0.5
) -> bool:
    """Probes a loopback URL to check if it is an owned QuantDesk API instance (§16.1)."""
    import urllib.error
    import urllib.request

    health_url = f"{base_url.rstrip('/')}/api/v1/system/status"
    req = urllib.request.Request(health_url, headers={"User-Agent": "QuantDesk-Launcher/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as response:
            if response.status == 200:
                data = json.loads(response.read().decode("utf-8"))
                return bool(data.get("app") == "QuantDesk" or "engine_state" in data)
    except Exception:
        # Also try /api/v1/health
        try:
            fallback_url = f"{base_url.rstrip('/')}/api/v1/health"
            req_fb = urllib.request.Request(
                fallback_url, headers={"User-Agent": "QuantDesk-Launcher/1.0"}
            )
            with urllib.request.urlopen(req_fb, timeout=timeout_sec) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode("utf-8"))
                    return bool(data.get("status") == "ok")
        except Exception:
            return False
    return False


class SupervisorManager:
    """Coordinates child processes, exclusive account locks, and health checks (§16.1, §16.2)."""

    def __init__(
        self,
        account_dir: Path | str,
        account_name: str = "demo",
        mode: str = "DEMO",
        preferred_port: int = DEFAULT_PORT,
    ):
        self.account_dir = Path(account_dir).resolve()
        self.account_name = account_name
        self.mode = mode
        self.preferred_port = preferred_port
        self.run_dir = self.account_dir / "run"
        self.logs_dir = self.account_dir / "logs"
        self.instance_file = self.run_dir / "active_instance.json"

        self.lock = AccountLock(self.account_dir)
        self.heartbeat_monitor = HeartbeatMonitor()
        self.watchdog = SupervisedRecoveryWatchdog()

        self.api_port: int = preferred_port
        self.base_url: str = f"http://{DEFAULT_HOST}:{self.api_port}"
        self._child_processes: list[subprocess.Popen[bytes]] = []
        self._is_running = False

    @property
    def is_running(self) -> bool:
        return self._is_running

    def check_existing_instance(self) -> ActiveInstanceInfo | None:
        """Checks if another QuantDesk instance is already active for this account."""
        if not self.instance_file.exists():
            return None

        try:
            data = json.loads(self.instance_file.read_text(encoding="utf-8"))
            pid = int(data.get("pid", 0))
            port = int(data.get("api_port", 0))
            url = str(data.get("base_url", f"http://{DEFAULT_HOST}:{port}"))

            # Check if PID is still alive
            pid_alive = False
            if pid == os.getpid():
                pid_alive = True
            elif sys.platform == "win32":
                import ctypes
                handle = ctypes.windll.kernel32.OpenProcess(
                    0x0400, False, pid  # PROCESS_QUERY_INFORMATION
                )
                if handle:
                    ctypes.windll.kernel32.CloseHandle(handle)
                    pid_alive = True
            else:
                try:
                    os.kill(pid, 0)
                    pid_alive = True
                except OSError:
                    pid_alive = False

            if pid_alive:
                return ActiveInstanceInfo(
                    pid=pid,
                    api_port=port,
                    base_url=url,
                    account=data.get("account", self.account_name),
                    mode=data.get("mode", self.mode),
                    epoch=int(data.get("epoch", 0)),
                    started_at_ns=int(data.get("started_at_ns", 0)),
                )
        except Exception:
            pass

        return None

    def start(self, spawn_api: bool = True) -> int:
        """Acquires lock, resolves port, writes instance metadata, and starts supervisor.

        Returns:
            The acquired ownership epoch integer.

        Raises:
            AccountLockedError: If directory is already locked by another process.
        """
        if self._is_running:
            return self.lock.epoch

        # 1. Acquire exclusive directory lock
        epoch = self.lock.acquire()

        # 2. Determine available port (handles collision detection per §16.1)
        self.api_port = find_free_loopback_port(self.preferred_port, DEFAULT_HOST)
        self.base_url = f"http://{DEFAULT_HOST}:{self.api_port}"

        # 3. Create run and logs directories
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)

        # 4. Write active instance metadata
        instance_data: dict[str, Any] = {
            "pid": os.getpid(),
            "api_port": self.api_port,
            "base_url": self.base_url,
            "account": self.account_name,
            "mode": self.mode,
            "epoch": epoch,
            "started_at_ns": time.time_ns(),
        }
        self.instance_file.write_text(json.dumps(instance_data, indent=2), encoding="utf-8")

        if spawn_api:
            cmd = [
                sys.executable,
                "-m",
                "uvicorn",
                "quantdesk.api.app:app",
                "--host",
                DEFAULT_HOST,
                "--port",
                str(self.api_port),
            ]
            self.spawn_hidden_process(cmd, "api")

        self._is_running = True
        return epoch

    def spawn_hidden_process(
        self,
        command: list[str],
        log_name: str,
        env_extra: dict[str, str] | None = None,
    ) -> subprocess.Popen[bytes]:
        """Launches a child process hidden without flashing console windows on Windows (§16.1)."""
        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NO_WINDOW  # 0x08000000

        log_path = self.logs_dir / f"{log_name}.log"
        log_file = open(log_path, "a+b")  # noqa: SIM115

        env = dict(os.environ)
        if env_extra:
            env.update(env_extra)

        proc = subprocess.Popen(
            command,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
            env=env,
        )
        self._child_processes.append(proc)
        return proc

    def stop(self, timeout_sec: float = 5.0) -> None:
        """Safely stops all managed child processes, removes instance file, and releases lock."""
        if not self._is_running:
            return

        # 1. Terminate all child processes
        for proc in self._child_processes:
            if proc.poll() is None:
                with contextlib.suppress(OSError):
                    proc.terminate()

        # 2. Wait for exit
        start_wait = time.monotonic()
        for proc in self._child_processes:
            if proc.poll() is None:
                remaining = max(0.1, timeout_sec - (time.monotonic() - start_wait))
                try:
                    proc.wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    with contextlib.suppress(OSError):
                        proc.kill()

        self._child_processes.clear()

        # 3. Remove active instance record
        if self.instance_file.exists():
            with contextlib.suppress(OSError):
                self.instance_file.unlink()

        # 4. Release exclusive OS directory lock
        self.lock.release()
        self._is_running = False

    def __enter__(self) -> SupervisorManager:
        self.start(spawn_api=False)
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.stop()
