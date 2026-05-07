"""Tests for :mod:`exlab_wizard.tray.window_launcher`. Backend Spec §4.3.2."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from exlab_wizard.tray.window_launcher import (
    WindowLauncher,
    _resolve_window_executable,
)


def _python_window_argv() -> list[str]:
    """Return an argv that runs a tiny window stand-in (a sleep)."""
    # A no-op subprocess that exits when stdin closes -- we feed DEVNULL,
    # so it sleeps for a moment, plenty of time to inspect ``is_alive``.
    return [
        sys.executable,
        "-c",
        "import time; time.sleep(2)",
    ]


def test_open_spawns_subprocess(tmp_path: Path) -> None:
    launcher = WindowLauncher(state_dir=tmp_path)
    fake_argv = _python_window_argv()
    with patch.object(WindowLauncher, "_argv", return_value=fake_argv):
        launcher.open()
        try:
            assert launcher.is_alive
            assert isinstance(launcher.pid, int)
        finally:
            launcher.close()
        assert launcher.is_alive is False


def test_close_terminates_subprocess(tmp_path: Path) -> None:
    launcher = WindowLauncher(state_dir=tmp_path)
    with patch.object(WindowLauncher, "_argv", return_value=_python_window_argv()):
        launcher.open()
    pid = launcher.pid
    assert pid is not None
    launcher.close()
    assert launcher.is_alive is False
    assert launcher.pid is None


def test_close_is_idempotent(tmp_path: Path) -> None:
    launcher = WindowLauncher(state_dir=tmp_path)
    launcher.close()  # No proc -- safe.
    launcher.close()
    assert launcher.is_alive is False


def test_open_focuses_existing(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    launcher = WindowLauncher(state_dir=tmp_path)
    with patch.object(WindowLauncher, "_argv", return_value=_python_window_argv()):
        launcher.open()
        # Second open should NOT spawn a second subprocess (single-instance).
        with caplog.at_level("INFO", logger="exlab_wizard.tray.window_launcher"):
            launcher.open()
        launcher.close()
    assert any("focus requested" in rec.getMessage() for rec in caplog.records)


def test_resolve_window_executable_prefers_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXLAB_WINDOW_EXECUTABLE", "/path/to/window-bin")
    assert _resolve_window_executable() == ["/path/to/window-bin"]


def test_resolve_window_executable_uses_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EXLAB_WINDOW_EXECUTABLE", raising=False)
    monkeypatch.setattr(
        "exlab_wizard.tray.window_launcher.shutil.which",
        lambda _: "/usr/local/bin/exlab-wizard-window",
    )
    assert _resolve_window_executable() == ["/usr/local/bin/exlab-wizard-window"]


def test_resolve_window_executable_falls_back_to_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EXLAB_WINDOW_EXECUTABLE", raising=False)
    monkeypatch.setattr("exlab_wizard.tray.window_launcher.shutil.which", lambda _: None)
    argv = _resolve_window_executable()
    assert argv[1:] == ["-m", "exlab_wizard.window.main"]
    assert argv[0] == sys.executable


def test_window_executable_override(tmp_path: Path) -> None:
    launcher = WindowLauncher(
        window_executable="/dev/null",
        state_dir=tmp_path,
    )
    assert launcher._argv() == ["/dev/null"]


def test_argv_falls_through_resolve_helper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Default constructor with no override delegates to the resolver."""
    launcher = WindowLauncher(state_dir=tmp_path)
    monkeypatch.setattr(
        "exlab_wizard.tray.window_launcher._resolve_window_executable",
        lambda: ["/sentinel/window-bin"],
    )
    assert launcher._argv() == ["/sentinel/window-bin"]


def test_pid_is_none_before_open(tmp_path: Path) -> None:
    launcher = WindowLauncher(state_dir=tmp_path)
    assert launcher.pid is None
    assert launcher.is_alive is False


def test_close_kills_unresponsive_process(tmp_path: Path) -> None:
    launcher = WindowLauncher(state_dir=tmp_path)

    class _ZombieProc:
        def __init__(self) -> None:
            self.pid = 12345
            self._alive = True
            self.terminate_called = False
            self.kill_called = False

        def poll(self) -> int | None:
            return None if self._alive else 0

        def terminate(self) -> None:
            self.terminate_called = True

        def wait(self, *, timeout: float | None = None) -> int:
            _ = timeout
            if self.terminate_called and not self.kill_called:
                # Simulate slow process: terminate timeout, kill works.
                raise subprocess.TimeoutExpired(cmd="x", timeout=timeout or 1.0)
            self._alive = False
            return 0

        def kill(self) -> None:
            self.kill_called = True

    zombie = _ZombieProc()
    launcher._proc = zombie  # type: ignore[assignment]
    launcher.close()
    assert zombie.terminate_called
    assert zombie.kill_called
