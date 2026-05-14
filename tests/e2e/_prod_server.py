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
import unittest.mock
from contextlib import closing
from pathlib import Path

import httpx


def free_port() -> int:
    """Return a free TCP port on 127.0.0.1."""
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def prod_app_env(home: Path) -> dict[str, str]:
    """Build a hermetic environment for a spawned production-app process.

    Every OS-specific base directory ``exlab_wizard.paths`` consults is
    pinned under ``home``, so the spawned app writes its config, state,
    cache and data exclusively inside the test's tmp tree -- regardless
    of whatever ``XDG_*`` / ``APPDATA`` values the CI runner exports.
    ``HOME`` / ``USERPROFILE`` cover ``Path.home()`` on POSIX / Windows.
    """
    repo_root = Path(__file__).resolve().parents[2]
    return {
        **os.environ,
        "HOME": str(home),
        "USERPROFILE": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_STATE_HOME": str(home / ".local" / "state"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "APPDATA": str(home / "AppData" / "Roaming"),
        "LOCALAPPDATA": str(home / "AppData" / "Local"),
        "PYTHONPATH": str(repo_root) + os.pathsep + os.environ.get("PYTHONPATH", ""),
    }


def resolve_config_path(home: Path) -> Path:
    """Where the spawned production app writes ``config.yaml``.

    Computed by calling the real ``exlab_wizard.paths.os_config_path``
    under the same environment the subprocess runs with, so the path
    can never drift from the app's own resolution across macOS / Linux
    / Windows (the e2e suite runs on Linux CI but developers run it on
    macOS).
    """
    from exlab_wizard.paths import os_config_path

    with unittest.mock.patch.dict(os.environ, prod_app_env(home), clear=True):
        return os_config_path()


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
        self._env = prod_app_env(home)

    @property
    def config_path(self) -> Path:
        """Where the wizard writes ``config.yaml`` under the redirected HOME."""
        return resolve_config_path(self.home)

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
