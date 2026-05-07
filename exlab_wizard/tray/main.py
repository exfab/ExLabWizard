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

import asyncio
import sys
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


def _build_default_app() -> Any:
    """Return the production FastAPI app for the tray.

    Imported lazily so the tray module's import graph stays light --
    importing FastAPI / NiceGUI on a CI worker that only runs unit
    tests for a single helper is wasteful.
    """
    from exlab_wizard.api.app import create_app

    return create_app()


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
    fastapi_app = app if app is not None else _build_default_app()
    server_runner = ServerRunner(app=fastapi_app, state_dir=state_dir)
    window_launcher = WindowLauncher(state_dir=state_dir)
    notification_bus = NotificationBus()
    status_ticker = StatusTicker()
    autostart = AutostartManager()
    quit_coordinator = QuitCoordinator(
        server_runner=server_runner,
        window_launcher=window_launcher,
        session_store=None,
        nas_sync=None,
    )
    return TrayApp(
        server_runner=server_runner,
        window_launcher=window_launcher,
        quit_coordinator=quit_coordinator,
        status_ticker=status_ticker,
        notification_bus=notification_bus,
        autostart=autostart,
    )


def main(argv: list[str] | None = None) -> int:
    """``exlab-wizard-tray`` entry point.

    The main thread runs pystray; the FastAPI server runs on a worker
    thread inside :class:`ServerRunner`. The function returns the exit
    code (0 on clean tray-driven Quit).
    """
    _ = argv  # CLI parsing is expanded in a later phase; stable signature here.
    configure_logging()

    from exlab_wizard.paths import ensure_state_dir

    state_dir = ensure_state_dir()
    app = _build_default_components(state_dir=state_dir)
    try:
        return app.run()
    except KeyboardInterrupt:
        _log.info("KeyboardInterrupt; shutting down")
        app.shutdown()
        return 0


if __name__ == "__main__":  # pragma: no cover -- script entrypoint
    sys.exit(main())
