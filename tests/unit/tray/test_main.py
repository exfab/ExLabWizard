"""Tests for :mod:`exlab_wizard.tray.main`. Backend Spec §4.3.2."""

from __future__ import annotations

import os
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
    class _FakeApp:
        state = MagicMock(dependencies=None)

    fake_app = _FakeApp()
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


def test_build_default_app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``_build_default_app`` builds deps, calls ``create_app``, mounts NiceGUI."""
    from exlab_wizard.tray import main as tray_main

    sentinel_app = MagicMock(name="fake_app")
    fake_deps = MagicMock(validator=None)
    create_app_calls: list[dict[str, Any]] = []
    mount_calls: list[dict[str, Any]] = []

    def _fake_create_app(**kwargs: Any) -> Any:
        create_app_calls.append(kwargs)
        return sentinel_app

    def _fake_mount_ui(app: Any, *, storage_secret: str) -> None:
        mount_calls.append({"app": app, "storage_secret": storage_secret})

    monkeypatch.setattr("exlab_wizard.api.app.create_app", _fake_create_app)
    monkeypatch.setattr(
        "exlab_wizard.tray.dependencies.build_production_dependencies",
        lambda _state_dir: fake_deps,
    )
    monkeypatch.setattr(
        "exlab_wizard.tray.storage_secret.load_or_create_storage_secret",
        lambda _state_dir: "test-secret",
    )
    monkeypatch.setattr("exlab_wizard.ui.mount.mount_ui", _fake_mount_ui)

    result = tray_main._build_default_app(tmp_path)
    assert result is sentinel_app
    assert create_app_calls == [{"dependencies": fake_deps, "start_audit_task": False}]
    assert mount_calls == [{"app": sentinel_app, "storage_secret": "test-secret"}]


def test_build_default_components_falls_back_to_default_app(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When ``app`` is None, the helper calls ``_build_default_app``."""
    from exlab_wizard.tray import main as tray_main

    sentinel = MagicMock(state=MagicMock(dependencies=None))
    monkeypatch.setattr(tray_main, "_build_default_app", lambda _state_dir: sentinel)
    tray = tray_main._build_default_components(state_dir=tmp_path)
    assert tray.server_runner is not None


def test_parse_argv_defaults_no_smoke() -> None:
    """No flags -> ``smoke`` is False, ``no_autostart_prompt`` is False."""
    from exlab_wizard.tray.main import _parse_argv

    args = _parse_argv([])
    assert args.smoke is False
    assert args.no_autostart_prompt is False


def test_parse_argv_smoke_flag() -> None:
    """``--smoke`` sets the smoke flag."""
    from exlab_wizard.tray.main import _parse_argv

    args = _parse_argv(["--smoke"])
    assert args.smoke is True


def test_parse_argv_no_autostart_prompt_silently_accepted() -> None:
    """``--no-autostart-prompt`` is silently accepted (reserved)."""
    from exlab_wizard.tray.main import _parse_argv

    args = _parse_argv(["--no-autostart-prompt"])
    assert args.no_autostart_prompt is True


def test_run_smoke_starts_server_and_stops_on_signal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``_run_smoke`` boots the server, blocks on a stop event, and stops cleanly."""
    import threading

    from exlab_wizard.tray import main as tray_main

    started: list[bool] = []
    stopped: list[bool] = []

    class _SmokeRunner:
        def __init__(self, *, app: Any, state_dir: Path) -> None:
            self.port = 9999

        def start(self) -> int:
            started.append(True)
            return self.port

        def stop(self) -> None:
            stopped.append(True)

    monkeypatch.setattr(tray_main, "ServerRunner", _SmokeRunner)
    monkeypatch.setattr(tray_main, "_build_default_app", lambda _state_dir: object())

    # Replace threading.Event with a pre-set event so wait() returns
    # immediately and we don't block the test.
    event = threading.Event()
    event.set()
    monkeypatch.setattr(tray_main.threading, "Event", lambda: event)

    rc = tray_main._run_smoke(state_dir=tmp_path)
    assert rc == 0
    assert started == [True]
    assert stopped == [True]


def test_main_smoke_dispatches_to_run_smoke(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``main(["--smoke"])`` calls ``_run_smoke`` instead of the icon loop."""
    from exlab_wizard.tray import main as tray_main

    monkeypatch.setattr(tray_main, "configure_logging", lambda: None)
    monkeypatch.setattr("exlab_wizard.paths.ensure_state_dir", lambda: tmp_path)

    called_with: list[Path] = []

    def _fake_run_smoke(state_dir: Path) -> int:
        called_with.append(state_dir)
        return 0

    monkeypatch.setattr(tray_main, "_run_smoke", _fake_run_smoke)
    rc = tray_main.main(["--smoke"])
    assert rc == 0
    assert called_with == [tmp_path]


def test_parse_argv_version_flag() -> None:
    """``--version`` sets the version flag."""
    from exlab_wizard.tray.main import _parse_argv

    args = _parse_argv(["--version"])
    assert args.version is True


def test_main_version_prints_version_and_exits_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``main(["--version"])`` prints the package version and returns 0."""
    from exlab_wizard import __version__
    from exlab_wizard.tray.main import main

    rc = main(["--version"])
    assert rc == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == __version__


# ---------------------------------------------------------------------------
# --test / --add-test-samples
# ---------------------------------------------------------------------------


def test_parse_argv_defaults_no_test_flags() -> None:
    """No flags -> both test-mode flags are False."""
    from exlab_wizard.tray.main import _parse_argv

    args = _parse_argv([])
    assert args.test is False
    assert args.add_test_samples is False


def test_parse_argv_test_flag() -> None:
    from exlab_wizard.tray.main import _parse_argv

    args = _parse_argv(["--test"])
    assert args.test is True
    assert args.add_test_samples is False


def test_parse_argv_test_with_samples() -> None:
    from exlab_wizard.tray.main import _parse_argv

    args = _parse_argv(["--test", "--add-test-samples"])
    assert args.test is True
    assert args.add_test_samples is True


def test_parse_argv_samples_without_test_errors(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--add-test-samples`` alone must be rejected via parser.error (exit 2)."""
    from exlab_wizard.tray.main import _parse_argv

    with pytest.raises(SystemExit) as exc:
        _parse_argv(["--add-test-samples"])
    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert "--add-test-samples requires --test" in captured.err


def test_main_test_flag_sets_env_and_bootstraps_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``main(["--test"])`` sets EXLAB_WIZARD_TEST_MODE=1 and writes a starter config.

    The env var must be set *before* any paths.py helper is called so the
    state-dir lookup and config-path lookup both land under the suffixed
    sandbox. We stub ``_build_default_components`` and ``TrayApp.run`` so
    no real server boots.
    """
    from exlab_wizard import paths
    from exlab_wizard.tray import main as tray_main

    monkeypatch.delenv(paths.TEST_MODE_ENV, raising=False)
    # Redirect HOME so os_config_path() / ensure_state_dir() land in tmp_path.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "APPDATA", "LOCALAPPDATA"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr("sys.platform", "linux")

    monkeypatch.setattr(tray_main, "configure_logging", lambda: None)
    monkeypatch.setattr(tray_main, "_build_default_components", lambda **_: _make_tray())
    monkeypatch.setattr(TrayApp, "run", lambda self, **_: 0)

    rc = tray_main.main(["--test"])
    assert rc == 0
    assert os.environ[paths.TEST_MODE_ENV] == "1"

    # Config landed under the '-test' sandbox.
    cfg_path = tmp_path / ".config" / "exlab-wizard-test" / "config.yaml"
    assert cfg_path.is_file()

    # The written config has the sandbox paths and an empty LIMS (user must
    # still implement the LMS endpoint via Settings).
    from exlab_wizard.config.loader import load_config

    cfg = load_config(cfg_path)
    sandbox = cfg_path.parent
    assert cfg.paths.local_root == str(sandbox / "local")
    assert cfg.paths.templates_dir == str(sandbox / "templates")
    assert cfg.paths.plugin_dir == str(sandbox / "plugins")
    assert cfg.orchestrator.staging_root == str(sandbox / "staging")
    assert cfg.orchestrator.label == "test-workstation"
    assert cfg.lims.endpoint == ""
    assert cfg.lims.email == ""
    assert cfg.equipment == []

    # Preseeded directories exist so first-launch path lookups succeed.
    for sub in ("local", "templates", "plugins", "staging"):
        assert (sandbox / sub).is_dir()


def test_main_test_does_not_overwrite_existing_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Re-running ``--test`` must preserve any edits the user made in-session."""
    from exlab_wizard import paths
    from exlab_wizard.tray import main as tray_main

    monkeypatch.delenv(paths.TEST_MODE_ENV, raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "APPDATA", "LOCALAPPDATA"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr("sys.platform", "linux")

    monkeypatch.setattr(tray_main, "configure_logging", lambda: None)
    monkeypatch.setattr(tray_main, "_build_default_components", lambda **_: _make_tray())
    monkeypatch.setattr(TrayApp, "run", lambda self, **_: 0)

    cfg_path = tmp_path / ".config" / "exlab-wizard-test" / "config.yaml"
    cfg_path.parent.mkdir(parents=True)
    sentinel = "paths:\n  local_root: /already/here\n"
    cfg_path.write_text(sentinel, encoding="utf-8")

    tray_main.main(["--test"])

    # File untouched -- bootstrap is a no-op when a config already exists.
    assert cfg_path.read_text(encoding="utf-8") == sentinel


def test_main_test_with_samples_adds_equipment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from exlab_wizard import paths
    from exlab_wizard.tray import main as tray_main

    monkeypatch.delenv(paths.TEST_MODE_ENV, raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "APPDATA", "LOCALAPPDATA"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr("sys.platform", "linux")

    monkeypatch.setattr(tray_main, "configure_logging", lambda: None)
    monkeypatch.setattr(tray_main, "_build_default_components", lambda **_: _make_tray())
    monkeypatch.setattr(TrayApp, "run", lambda self, **_: 0)

    tray_main.main(["--test", "--add-test-samples"])

    from exlab_wizard.config.loader import load_config

    cfg = load_config(tmp_path / ".config" / "exlab-wizard-test" / "config.yaml")
    assert len(cfg.equipment) == 1
    assert cfg.equipment[0].id == "TESTRIG"


def test_main_without_test_does_not_set_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Sanity: running without --test leaves the env var unset and writes no test config."""
    from exlab_wizard import paths
    from exlab_wizard.tray import main as tray_main

    monkeypatch.delenv(paths.TEST_MODE_ENV, raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "APPDATA", "LOCALAPPDATA"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr("sys.platform", "linux")

    monkeypatch.setattr(tray_main, "configure_logging", lambda: None)
    monkeypatch.setattr(tray_main, "_build_default_components", lambda **_: _make_tray())
    monkeypatch.setattr(TrayApp, "run", lambda self, **_: 0)

    tray_main.main([])
    assert paths.TEST_MODE_ENV not in os.environ
    assert not (tmp_path / ".config" / "exlab-wizard-test").exists()
