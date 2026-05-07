"""Integration test: tray <-> window lifecycle round-trip. Backend Spec §4.3.2.

The test exercises the full handoff:

1. Build a real FastAPI app via :func:`create_app`.
2. Start the in-process server via :class:`ServerRunner`.
3. Issue an HTTP request against the bound port to confirm the server
   actually serves traffic.
4. Read the live ``server.json`` from the tray's state dir using the
   window's :func:`read_server_handshake` to confirm the schema matches
   what the window expects.
5. Run :class:`QuitCoordinator.quit` and assert ``server.json`` has been
   removed atomically.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

# httpx is already a runtime dep -- used here as the in-process HTTP client.
import httpx

from exlab_wizard.api.app import create_app
from exlab_wizard.tray.quit_coordinator import QuitCoordinator
from exlab_wizard.tray.server_runner import ServerRunner
from exlab_wizard.window.main import read_server_handshake


def _wait_for_health(port: int, *, timeout: float = 5.0) -> None:
    """Poll ``/api/v1/health`` until the server is accepting requests."""
    deadline = time.monotonic() + timeout
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with httpx.Client(timeout=1.0) as client:
                resp = client.get(f"http://127.0.0.1:{port}/api/v1/health")
                if resp.status_code == 200:
                    return
        except Exception as exc:
            last_exc = exc
            time.sleep(0.05)
    msg = f"server did not become reachable on port {port}: {last_exc!r}"
    raise AssertionError(msg)


def test_tray_lifecycle_round_trip(tmp_path: Path) -> None:
    app = create_app()
    runner = ServerRunner(app=app, state_dir=tmp_path)
    port = runner.start()
    try:
        _wait_for_health(port)

        # The window-side reader sees the same schema the tray wrote.
        handshake = read_server_handshake(tmp_path)
        assert handshake is not None
        assert handshake.port == port
        # PID matches our own process.
        import os

        assert handshake.pid == os.getpid()
        assert handshake.started_at  # non-empty ISO 8601 string

        # Now run the graceful-shutdown protocol with idle predicates.
        coord = QuitCoordinator(
            server_runner=runner,
            window_launcher=None,
            session_store=None,
            nas_sync=None,
            timeout_seconds=0.0,
            sigterm_timeout_seconds=0.0,
            poll_interval_seconds=0.01,
        )
        asyncio.run(coord.quit())

        # server.json is gone, the worker thread has joined.
        assert not (tmp_path / "server.json").exists()
        assert runner.is_running is False
    finally:
        # Defensive cleanup -- if the assertions short-circuited.
        runner.stop()


def test_tray_lifecycle_window_handshake_reads_server_json(tmp_path: Path) -> None:
    app = create_app()
    runner = ServerRunner(app=app, state_dir=tmp_path)
    runner.start()
    try:
        # The window subprocess (in production) reads server.json.
        # Here we simulate that read directly to verify the contract.
        handshake = read_server_handshake(tmp_path)
        assert handshake is not None
        url = f"http://127.0.0.1:{handshake.port}/api/v1/health"
        # The window would point pywebview at this URL.
        assert url.startswith("http://127.0.0.1:")
    finally:
        runner.stop()
