"""``exlab-wizard-tray`` console_scripts entry point. Backend Spec §4.3.2.

Wires the long-lived process:

1. Configure logging.
2. Build / load the FastAPI app.
3. Construct :class:`AutostartManager`,
   :class:`ServerRunner`, :class:`WindowLauncher`,
   :class:`QuitCoordinator`, :class:`StatusTicker`, and
   :class:`NotificationBus`.
4. Build the pystray :class:`Icon` and run its event loop.

The orchestration is intentionally light -- each component owns its own
lifecycle; ``main()`` is the assembly point. Tests cover the wiring by
constructing :class:`TrayApp` directly with stub components; the real
``main()`` resolves production wiring (FastAPI app + state dir) and
delegates.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import signal
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from exlab_wizard.logging import configure_logging, get_logger
from exlab_wizard.tray.autostart import AutostartManager
from exlab_wizard.tray.icon import build_icon
from exlab_wizard.tray.notifications import NotificationBus
from exlab_wizard.tray.quit_coordinator import QuitCoordinator
from exlab_wizard.tray.server_runner import ServerRunner
from exlab_wizard.tray.status import StatusTicker
from exlab_wizard.tray.window_launcher import WindowLauncher

__all__ = ["TrayApp", "main"]

_log = get_logger(__name__)


@dataclass
class TrayApp:
    """Bundle of long-lived tray components.

    The launcher constructs one and calls :meth:`run`; tests build one
    with stub components and exercise :meth:`shutdown` / :meth:`open`
    individually.
    """

    server_runner: ServerRunner
    window_launcher: WindowLauncher
    quit_coordinator: QuitCoordinator
    status_ticker: StatusTicker
    notification_bus: NotificationBus
    autostart: AutostartManager
    icon: Any = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start_server(self) -> int:
        """Start the in-process FastAPI server. Returns the bound port."""
        return self.server_runner.start()

    def open_window(self) -> None:
        """Spawn or focus the window subprocess."""
        self.window_launcher.open()

    def request_quit(self) -> None:
        """Trigger the graceful-shutdown protocol on the icon thread."""
        try:
            asyncio.run(self.quit_coordinator.quit())
        except RuntimeError:
            # Already inside an event loop -- schedule on it.
            loop = asyncio.get_event_loop()
            loop.run_until_complete(self.quit_coordinator.quit())
        if self.icon is not None:
            try:
                self.icon.stop()
            except Exception:
                _log.exception("icon.stop raised")

    def shutdown(self) -> None:
        """Tear down sub-components synchronously."""
        self.status_ticker.stop()
        self.notification_bus.cancel_all()
        self.window_launcher.close()
        self.server_runner.stop()

    def run(self, *, run_loop: Callable[[], None] | None = None) -> int:
        """Start the server, build the icon, run the pystray loop.

        ``run_loop`` is injected by tests so they don't actually call
        ``Icon.run`` (which would block on a real backend). Returns the
        exit code.
        """
        self.start_server()
        self.status_ticker.start()
        self.icon = build_icon(
            on_open=self.open_window,
            on_quit=self.request_quit,
            status_provider=self.status_ticker.tick_once,
        )
        if run_loop is None:
            try:
                self.icon.run()
            except Exception:
                _log.exception("pystray icon loop raised")
                return 1
        else:
            run_loop()
        self.shutdown()
        return 0


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------


def _build_default_app(state_dir: Path) -> Any:
    """Return the production FastAPI app for the tray.

    Constructs the live :class:`AppDependencies` bundle (controller,
    validator, LIMS client, sync queue, plugin host, ...), wires them
    into the FastAPI app via ``create_app(dependencies=...)``, and
    mounts the NiceGUI wizard at ``/`` so the pywebview window renders
    the GUI instead of the API's 404 envelope. Imports are deferred to
    keep the tray module's import graph light.
    """
    from exlab_wizard.api.app import create_app
    from exlab_wizard.tray.dependencies import build_production_dependencies
    from exlab_wizard.tray.storage_secret import load_or_create_storage_secret
    from exlab_wizard.ui.mount import mount_ui

    deps = build_production_dependencies(state_dir)
    app = create_app(
        dependencies=deps,
        start_audit_task=deps.validator is not None,
    )
    mount_ui(app, storage_secret=load_or_create_storage_secret(state_dir))
    return app


def _build_default_components(
    *,
    state_dir: Path,
    app: Any | None = None,
) -> TrayApp:
    """Construct the production TrayApp wiring.

    Split out so :func:`main` can stay tiny and tests can call this
    helper with a stub state dir to exercise the production wiring
    against a tmp_path.
    """
    fastapi_app = app if app is not None else _build_default_app(state_dir)
    deps = getattr(fastapi_app.state, "dependencies", None)
    server_runner = ServerRunner(app=fastapi_app, state_dir=state_dir)
    window_launcher = WindowLauncher(state_dir=state_dir)
    notification_bus = NotificationBus()
    status_ticker = StatusTicker()
    autostart = AutostartManager()
    quit_coordinator = QuitCoordinator(
        server_runner=server_runner,
        window_launcher=window_launcher,
        session_store=getattr(deps, "session_store", None),
        nas_sync=getattr(deps, "nas_sync", None),
    )
    return TrayApp(
        server_runner=server_runner,
        window_launcher=window_launcher,
        quit_coordinator=quit_coordinator,
        status_ticker=status_ticker,
        notification_bus=notification_bus,
        autostart=autostart,
    )


def _parse_argv(argv: list[str] | None) -> argparse.Namespace:
    """Parse the tray's CLI arguments.

    The tray accepts:

    - ``--version`` -- print the package version and exit 0. Used by
      the CI build matrix as a minimal smoke check that the
      PyInstaller bundle produces a working binary.
    - ``--smoke`` -- server-only mode. Boots the FastAPI server,
      writes ``server.json``, then waits on SIGTERM/SIGINT. Skips
      pystray entirely so the tray works on headless runners with no
      display server. Useful for richer integration smoke flows that
      need to hit a live HTTP endpoint.
    - ``--no-autostart-prompt`` -- silently accepted; reserved for a
      future surface that suppresses the welcome card's autostart
      affordance during automated launches. Discarded today.
    """
    parser = argparse.ArgumentParser(prog="exlab-wizard-tray", add_help=True)
    parser.add_argument(
        "--version",
        action="store_true",
        help="Print the package version and exit.",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="Server-only mode: boot the FastAPI server, write server.json, wait on signal.",
    )
    parser.add_argument(
        "--no-autostart-prompt",
        action="store_true",
        help="Suppress the welcome card autostart prompt (no-op today; reserved).",
    )
    return parser.parse_args(argv)


def _run_smoke(state_dir: Path) -> int:
    """Server-only loop. Boots the FastAPI server, prints the published
    port, waits on SIGTERM/SIGINT, then stops cleanly.

    The CI smoke step needs the server to come up but does not need (and
    cannot drive) the pystray icon on a headless runner. This path
    bypasses pystray entirely.
    """
    fastapi_app = _build_default_app(state_dir)
    server_runner = ServerRunner(app=fastapi_app, state_dir=state_dir)
    port = server_runner.start()
    _log.info("smoke: server bound to port %d", port)
    print(f"exlab-wizard-tray smoke: server on port {port}", flush=True)

    stop_event = threading.Event()

    def _on_signal(signum: int, _frame: Any) -> None:
        _log.info("smoke: received signal %d; stopping", signum)
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        # Signals only register on the main thread; skip silently if
        # we're not it (defensive; smoke runs on the main thread).
        with contextlib.suppress(OSError, ValueError):
            signal.signal(sig, _on_signal)

    try:
        stop_event.wait()
    finally:
        server_runner.stop()
    return 0


def main(argv: list[str] | None = None) -> int:
    """``exlab-wizard-tray`` entry point.

    The main thread runs pystray; the FastAPI server runs on a worker
    thread inside :class:`ServerRunner`. The function returns the exit
    code (0 on clean tray-driven Quit).
    """
    args = _parse_argv(argv)
    if args.version:
        from exlab_wizard import __version__

        print(__version__)
        return 0
    configure_logging()

    from exlab_wizard.paths import ensure_state_dir

    state_dir = ensure_state_dir()
    if args.smoke:
        return _run_smoke(state_dir)

    app = _build_default_components(state_dir=state_dir)
    try:
        return app.run()
    except KeyboardInterrupt:
        _log.info("KeyboardInterrupt; shutting down")
        app.shutdown()
        return 0


if __name__ == "__main__":  # pragma: no cover -- script entrypoint
    sys.exit(main())
