"""Reusable spawn/restart harness for the *production* wizard app.

The e2e lifecycle test boots the real production app (via
``tests.e2e._prod_app:create_prod_app_factory``) with ``HOME`` pointed
at a fresh tmp directory, so ``paths.os_config_path`` /
``paths.os_state_path`` resolve under tmp -- a true fresh install.

It also needs to *restart* the process mid-test: the config-dependent
components (controller / lims_client / nas_sync) are built once at boot,
so a config.yaml written by the settings wizard only takes effect after
a relaunch. :class:`ProdServer` exposes ``start`` / ``restart`` / ``stop``
for exactly that.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path

import httpx


def free_port() -> int:
    """Return a free TCP port on 127.0.0.1."""
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def macos_config_path(home: Path) -> Path:
    """``paths.os_config_path()`` resolved under a redirected HOME (Darwin)."""
    return home / "Library" / "Application Support" / "exlab-wizard" / "config.yaml"


class ProdServer:
    """A spawnable / restartable production-app uvicorn process.

    ``home`` is the redirected ``$HOME``; the same value is reused across
    restarts so config / state persist between boots, exactly as a real
    tray relaunch would see them. ``base_url`` is stable across restarts
    because the port is fixed at construction.
    """

    def __init__(self, home: Path) -> None:
        self.home = home
        self.port = free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self._proc: subprocess.Popen[bytes] | None = None
        repo_root = Path(__file__).resolve().parents[2]
        self._env = {
            **os.environ,
            "HOME": str(home),
            "PYTHONPATH": str(repo_root) + os.pathsep + os.environ.get("PYTHONPATH", ""),
        }

    @property
    def config_path(self) -> Path:
        """Where the wizard writes ``config.yaml`` under the redirected HOME."""
        return macos_config_path(self.home)

    def start(self, *, timeout: float = 30.0) -> bool:
        """Spawn uvicorn and block until ``/api/v1/health`` answers 200.

        Returns ``True`` when the server came up, ``False`` otherwise
        (the caller typically ``pytest.skip``s on ``False``).
        """
        self._proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "tests.e2e._prod_app:create_prod_app_factory",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
                "--log-level",
                "error",
            ],
            env=self._env,
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                resp = httpx.get(f"{self.base_url}/api/v1/health", timeout=1.0)
                if resp.status_code == 200:
                    return True
            except Exception:
                pass
            time.sleep(0.5)
        self.stop()
        return False

    def stop(self) -> None:
        """Terminate the uvicorn process. Idempotent."""
        if self._proc is None:
            return
        self._proc.terminate()
        try:
            self._proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait(timeout=5)
        self._proc = None

    def restart(self, *, timeout: float = 30.0) -> bool:
        """Stop and re-spawn the process with the same HOME / port."""
        self.stop()
        return self.start(timeout=timeout)
