"""Programmatic uvicorn launcher with atomic ``server.json`` writes. Backend Spec §4.3.2.

The tray process owns the FastAPI server in-process. :class:`ServerRunner`
encapsulates the lifecycle:

1. Pick a free localhost port from the OS at start time (Backend §15.3.1).
2. Launch ``uvicorn.Server.run`` on a dedicated worker thread so the
   pystray main-thread event loop is unaffected.
3. Atomically write ``<state_dir>/server.json`` with ``{port, pid,
   started_at}`` so :mod:`exlab_wizard.window` (a separate process) can
   discover the live server (Backend §4.2 -- "Window<->server discovery").
4. On stop, signal uvicorn to exit and delete the state file.

The atomic write follows the §4.4.5 idiom (write tmp, fsync, replace) so
a crash during the write never leaves a half-written state file behind
that the window subprocess could try to parse.
"""

from __future__ import annotations

import contextlib
import json
import socket
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from exlab_wizard.constants import SERVER_STATE_FILE
from exlab_wizard.io import atomic_write_bytes
from exlab_wizard.logging import get_logger
from exlab_wizard.utils.time import utc_now_iso

if TYPE_CHECKING:
    from fastapi import FastAPI

__all__ = ["SERVER_STATE_FILE", "ServerRunner", "pick_free_port"]

_log = get_logger(__name__)


def pick_free_port() -> int:
    """Return a free localhost port from the OS.

    Binds a SOCK_STREAM socket to ``("127.0.0.1", 0)`` and reads the
    OS-assigned port back, then closes the socket. Subject to the usual
    tiny race between close and re-bind by uvicorn -- acceptable in
    practice; the alternative is leaking the socket which uvicorn cannot
    accept ownership of.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class ServerRunner:
    """Starts uvicorn on a free localhost port and tracks its state file.

    Backend Spec §4.3.2 + §15.3.1.
    """

    def __init__(self, *, app: FastAPI, state_dir: Path) -> None:
        self._app = app
        self._state_dir = Path(state_dir)
        self._port: int | None = None
        # uvicorn.Server is typed as ``Any`` here so call sites can use
        # ``.run`` / ``.should_exit`` without per-attribute mypy ignores.
        self._server: Any = None
        self._thread: threading.Thread | None = None
        self._state_file: Path = self._state_dir / SERVER_STATE_FILE

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> int:
        """Pick a port, launch uvicorn in a worker thread, write server.json.

        Returns the chosen port. Idempotent in the sense that calling
        :meth:`start` a second time before :meth:`stop` raises
        ``RuntimeError`` -- the runner manages exactly one server.
        """
        if self._server is not None:
            msg = "ServerRunner.start called while a server is already running"
            raise RuntimeError(msg)

        port = pick_free_port()
        _config, server = self._build_uvicorn(port)

        thread = threading.Thread(
            target=server.run,
            name="exlab-uvicorn",
            daemon=True,
        )
        thread.start()

        self._port = port
        self._server = server
        self._thread = thread
        self._write_state_file(port)
        _log.info("server started on 127.0.0.1:%d", port)
        return port

    def stop(self) -> None:
        """Signal uvicorn to exit, join the worker thread, delete server.json.

        Idempotent: a second call is a no-op.
        """
        if self._server is None:
            return
        # uvicorn.Server has a ``should_exit`` attribute that the run loop
        # polls; setting it triggers a clean shutdown that runs the
        # FastAPI lifespan teardown.
        with contextlib.suppress(AttributeError):
            self._server.should_exit = True
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=10.0)
        self._delete_state_file()
        self._server = None
        self._thread = None
        self._port = None
        _log.info("server stopped")

    @property
    def port(self) -> int:
        """Return the port the server is bound to.

        Raises ``RuntimeError`` when called before :meth:`start`.
        """
        if self._port is None:
            msg = "ServerRunner.port read before start()"
            raise RuntimeError(msg)
        return self._port

    @property
    def is_running(self) -> bool:
        """Return ``True`` while the worker thread is alive."""
        if self._thread is None:
            return False
        return self._thread.is_alive()

    @property
    def state_file(self) -> Path:
        """Return the absolute path of the ``server.json`` state file."""
        return self._state_file

    # ------------------------------------------------------------------
    # Internals (split out so tests can monkeypatch around real uvicorn)
    # ------------------------------------------------------------------

    def _build_uvicorn(self, port: int) -> tuple[Any, Any]:
        """Return ``(uvicorn.Config, uvicorn.Server)`` for the given port.

        Split out so unit tests can monkeypatch the import without
        wrestling with the real uvicorn dependency in CI. Typed as
        ``Any`` so the callers can use ``.run`` / ``.should_exit`` on
        the returned objects without further mypy noise.
        """
        import uvicorn

        config = uvicorn.Config(
            self._app,
            host="127.0.0.1",
            port=port,
            log_config=None,
            access_log=False,
        )
        server = uvicorn.Server(config)
        return config, server

    def _write_state_file(self, port: int) -> None:
        """Atomically write ``server.json``. Backend Spec §4.4.5 idiom."""
        import os

        self._state_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "port": port,
            "pid": os.getpid(),
            "started_at": utc_now_iso(),
        }
        data = json.dumps(payload).encode("utf-8")
        atomic_write_bytes(self._state_file, data)

    def _delete_state_file(self) -> None:
        """Best-effort delete; missing file is fine."""
        with contextlib.suppress(FileNotFoundError):
            self._state_file.unlink()
