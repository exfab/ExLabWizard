"""Tests for :mod:`exlab_wizard.tray.main`. Backend Spec §4.3.2."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from exlab_wizard.tray.main import TrayApp, _build_default_components


class _StubServerRunner:
    def __init__(self) -> None:
        self.start_called = False
        self.stop_called = False
        self.is_running = False
        self.port = 12345

    def start(self) -> int:
        self.start_called = True
        return self.port

    def stop(self) -> None:
        self.stop_called = True


class _StubWindowLauncher:
    def __init__(self) -> None:
        self.open_called = False
        self.close_called = False

    def open(self) -> None:
        self.open_called = True

    def close(self) -> None:
        self.close_called = True


class _StubQuitCoordinator:
    def __init__(self) -> None:
        self.quit_called = False

    async def quit(self, *, sigterm: bool = False) -> None:
        _ = sigterm
        self.quit_called = True


class _StubStatusTicker:
    def __init__(self) -> None:
        self.start_called = False
        self.stop_called = False
        self.tick_called = 0

    def start(self) -> None:
        self.start_called = True

    def stop(self) -> None:
        self.stop_called = True

    def tick_once(self) -> str:
        self.tick_called += 1
        return "Idle"


class _StubBus:
    def __init__(self) -> None:
        self.cancel_called = False

    def cancel_all(self) -> None:
        self.cancel_called = True


class _StubAutostart:
    pass


def _make_tray() -> TrayApp:
    return TrayApp(
        server_runner=_StubServerRunner(),  # type: ignore[arg-type]
        window_launcher=_StubWindowLauncher(),  # type: ignore[arg-type]
        quit_coordinator=_StubQuitCoordinator(),  # type: ignore[arg-type]
        status_ticker=_StubStatusTicker(),  # type: ignore[arg-type]
        notification_bus=_StubBus(),  # type: ignore[arg-type]
        autostart=_StubAutostart(),  # type: ignore[arg-type]
    )


def test_start_server_calls_runner() -> None:
    tray = _make_tray()
    port = tray.start_server()
    assert port == tray.server_runner.port
    assert tray.server_runner.start_called  # type: ignore[attr-defined]


def test_open_window_delegates() -> None:
    tray = _make_tray()
    tray.open_window()
    assert tray.window_launcher.open_called  # type: ignore[attr-defined]


def test_request_quit_runs_coordinator() -> None:
    tray = _make_tray()
    tray.request_quit()
    assert tray.quit_coordinator.quit_called  # type: ignore[attr-defined]


def test_request_quit_calls_icon_stop_when_set() -> None:
    tray = _make_tray()
    icon = MagicMock()
    tray.icon = icon
    tray.request_quit()
    icon.stop.assert_called_once()


def test_request_quit_handles_icon_stop_error() -> None:
    tray = _make_tray()
    tray.icon = MagicMock()
    tray.icon.stop.side_effect = RuntimeError("boom")
    # Must not propagate.
    tray.request_quit()


def test_shutdown_tears_components_down() -> None:
    tray = _make_tray()
    tray.shutdown()
    assert tray.status_ticker.stop_called  # type: ignore[attr-defined]
    assert tray.notification_bus.cancel_called  # type: ignore[attr-defined]
    assert tray.window_launcher.close_called  # type: ignore[attr-defined]
    assert tray.server_runner.stop_called  # type: ignore[attr-defined]


def test_run_wires_icon_and_invokes_run_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    tray = _make_tray()

    built: dict[str, Any] = {}

    def _fake_build_icon(*, on_open: Any, on_quit: Any, status_provider: Any) -> Any:
        built["on_open"] = on_open
        built["on_quit"] = on_quit
        built["status_provider"] = status_provider
        return MagicMock()

    monkeypatch.setattr("exlab_wizard.tray.main.build_icon", _fake_build_icon)

    invocations: list[str] = []

    def _fake_loop() -> None:
        invocations.append("loop")

    rc = tray.run(run_loop=_fake_loop)
    assert rc == 0
    assert tray.server_runner.start_called  # type: ignore[attr-defined]
    assert tray.status_ticker.start_called  # type: ignore[attr-defined]
    # Compare the underlying methods (bound-method identity is fresh per access).
    assert built["on_open"].__func__ is TrayApp.open_window
    assert built["on_quit"].__func__ is TrayApp.request_quit
    assert built["status_provider"] == tray.status_ticker.tick_once
    assert invocations == ["loop"]
    # Shutdown was called by run().
    assert tray.server_runner.stop_called  # type: ignore[attr-defined]


def test_build_default_components(tmp_path: Path) -> None:
    fake_app = object()
    tray = _build_default_components(state_dir=tmp_path, app=fake_app)
    assert isinstance(tray, TrayApp)
    assert tray.server_runner is not None
    assert tray.window_launcher is not None
    assert tray.quit_coordinator is not None


def test_main_uses_run_helper(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``main()`` builds default components and calls :meth:`TrayApp.run`."""
    from exlab_wizard.tray import main as tray_main

    monkeypatch.setattr(tray_main, "configure_logging", lambda: None)
    monkeypatch.setattr("exlab_wizard.paths.ensure_state_dir", lambda: tmp_path)

    captured: dict[str, Any] = {}

    def _fake_build(*, state_dir: Path, app: Any | None = None) -> TrayApp:
        captured["state_dir"] = state_dir
        captured["app"] = app
        return _make_tray()

    monkeypatch.setattr(tray_main, "_build_default_components", _fake_build)

    # Patch TrayApp.run to skip the icon assembly.
    monkeypatch.setattr(TrayApp, "run", lambda self, **_: 0)
    rc = tray_main.main([])
    assert rc == 0
    assert captured["state_dir"] == tmp_path


def test_main_handles_keyboard_interrupt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from exlab_wizard.tray import main as tray_main

    monkeypatch.setattr(tray_main, "configure_logging", lambda: None)
    monkeypatch.setattr("exlab_wizard.paths.ensure_state_dir", lambda: tmp_path)

    monkeypatch.setattr(tray_main, "_build_default_components", lambda **_: _make_tray())

    def _raise(self: TrayApp, **_kwargs: Any) -> int:
        _ = self
        raise KeyboardInterrupt

    monkeypatch.setattr(TrayApp, "run", _raise)
    rc = tray_main.main([])
    assert rc == 0


def test_run_returns_one_on_icon_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    tray = _make_tray()

    fake_icon = MagicMock()
    fake_icon.run.side_effect = RuntimeError("boom")

    monkeypatch.setattr(
        "exlab_wizard.tray.main.build_icon",
        lambda **_kwargs: fake_icon,
    )
    rc = tray.run()
    assert rc == 1


def test_request_quit_uses_running_loop_when_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``asyncio.run`` raises RuntimeError when a loop is running.

    The tray's ``request_quit`` falls back to the running loop's
    ``run_until_complete``. We cannot easily start a real loop here;
    instead we patch ``asyncio.run`` to raise so the fallback path
    is exercised.
    """
    import contextlib

    tray = _make_tray()

    def _fake_run(coro: Any) -> None:
        # Close the coroutine so we don't leak warnings about un-awaited coros.
        with contextlib.suppress(Exception):
            coro.close()
        raise RuntimeError("there is already a running loop")

    monkeypatch.setattr("exlab_wizard.tray.main.asyncio.run", _fake_run)

    class _FakeLoop:
        def __init__(self) -> None:
            self.completed: list[Any] = []

        def run_until_complete(self, coro: Any) -> None:
            self.completed.append(coro)
            with contextlib.suppress(Exception):
                coro.close()

    fake_loop = _FakeLoop()
    monkeypatch.setattr("exlab_wizard.tray.main.asyncio.get_event_loop", lambda: fake_loop)
    tray.request_quit()
    assert fake_loop.completed


def test_build_default_app(monkeypatch: pytest.MonkeyPatch) -> None:
    """``_build_default_app`` lazily imports and calls ``create_app``."""
    from exlab_wizard.tray import main as tray_main

    sentinel = object()

    def _fake_create_app() -> object:
        return sentinel

    monkeypatch.setattr("exlab_wizard.api.app.create_app", _fake_create_app)
    assert tray_main._build_default_app() is sentinel


def test_build_default_components_falls_back_to_default_app(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When ``app`` is None, the helper calls ``_build_default_app``."""
    from exlab_wizard.tray import main as tray_main

    sentinel = object()
    monkeypatch.setattr(tray_main, "_build_default_app", lambda: sentinel)
    tray = tray_main._build_default_components(state_dir=tmp_path)
    assert tray.server_runner is not None
