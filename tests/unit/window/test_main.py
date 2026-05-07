"""Tests for :mod:`exlab_wizard.window.main`. Backend Spec §15.3.2."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from exlab_wizard.window.main import (
    EXIT_OK,
    EXIT_STALE_STATE,
    ServerHandshake,
    is_pid_alive,
    main,
    read_server_handshake,
)


def _write_server_json(state_dir: Path, *, port: int = 1234, pid: int | None = None) -> Path:
    state_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "port": port,
        "pid": pid if pid is not None else os.getpid(),
        "started_at": "2026-05-07T00:00:00+00:00",
    }
    path = state_dir / "server.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_read_server_handshake_returns_none_when_missing(tmp_path: Path) -> None:
    assert read_server_handshake(tmp_path) is None


def test_read_server_handshake_returns_handshake(tmp_path: Path) -> None:
    _write_server_json(tmp_path, port=4242)
    handshake = read_server_handshake(tmp_path)
    assert handshake is not None
    assert handshake.port == 4242
    assert handshake.pid == os.getpid()


def test_read_server_handshake_returns_none_on_invalid_json(tmp_path: Path) -> None:
    (tmp_path / "server.json").write_text("not-json", encoding="utf-8")
    assert read_server_handshake(tmp_path) is None


def test_read_server_handshake_returns_none_on_missing_keys(tmp_path: Path) -> None:
    (tmp_path / "server.json").write_text(json.dumps({"only": "data"}), encoding="utf-8")
    assert read_server_handshake(tmp_path) is None


def test_main_returns_stale_when_no_state_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(state_dir=tmp_path)
    assert rc == EXIT_STALE_STATE
    captured = capsys.readouterr()
    assert "server.json" in captured.err


def test_main_returns_stale_on_dead_pid(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_server_json(tmp_path, pid=99)
    rc = main(state_dir=tmp_path, pid_alive=lambda _pid: False)
    assert rc == EXIT_STALE_STATE
    captured = capsys.readouterr()
    assert "dead PID" in captured.err


def test_main_hands_off_with_live_state(tmp_path: Path) -> None:
    _write_server_json(tmp_path, port=5555)
    record: list[ServerHandshake] = []

    def _handoff(handshake: ServerHandshake) -> int:
        record.append(handshake)
        return EXIT_OK

    rc = main(state_dir=tmp_path, handoff=_handoff)
    assert rc == EXIT_OK
    assert len(record) == 1
    assert record[0].port == 5555


def test_is_pid_alive_for_self() -> None:
    assert is_pid_alive(os.getpid()) is True


def test_is_pid_alive_rejects_zero() -> None:
    assert is_pid_alive(0) is False
    assert is_pid_alive(-1) is False


def test_is_pid_alive_for_dead_pid() -> None:
    # Use a clearly non-existent PID.
    assert is_pid_alive(2**31 - 1) is False


def test_is_pid_alive_falls_through_to_default(tmp_path: Path) -> None:
    """Default ``pid_alive`` lookup uses ``is_pid_alive``."""
    _write_server_json(tmp_path)
    record: list[ServerHandshake] = []

    def _handoff(h: ServerHandshake) -> int:
        record.append(h)
        return EXIT_OK

    rc = main(state_dir=tmp_path, handoff=_handoff)
    assert rc == EXIT_OK
    assert record


def test_default_state_dir_is_used(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("exlab_wizard.paths.ensure_state_dir", lambda: tmp_path)
    rc = main()
    assert rc == EXIT_STALE_STATE  # No server.json under tmp_path


def test_default_handoff_invokes_run_window(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_server_json(tmp_path)
    invocations: list[Any] = []

    def _fake_run_window(handshake: Any) -> int:
        invocations.append(handshake)
        return EXIT_OK

    monkeypatch.setattr("exlab_wizard.window.pywebview_app.run_window", _fake_run_window)
    rc = main(state_dir=tmp_path)
    assert rc == EXIT_OK
    assert invocations


def test_argv_is_ignored(tmp_path: Path) -> None:
    _write_server_json(tmp_path)
    rc = main(["--anything", "--here"], state_dir=tmp_path, handoff=lambda _h: EXIT_OK)
    assert rc == EXIT_OK


def test_server_handshake_round_trip() -> None:
    h = ServerHandshake(port=10, pid=20, started_at="2026")
    assert h.port == 10
    assert h.pid == 20
    assert h.started_at == "2026"


def test_posix_is_pid_alive_handles_permission() -> None:
    """A foreign-uid live PID counts as alive."""
    if sys.platform == "win32":
        pytest.skip("POSIX-only path")

    from exlab_wizard.window.main import _posix_is_pid_alive

    with patch("os.kill", side_effect=PermissionError):
        assert _posix_is_pid_alive(1) is True


def test_posix_is_pid_alive_handles_dead_pid() -> None:
    if sys.platform == "win32":
        pytest.skip("POSIX-only path")

    from exlab_wizard.window.main import _posix_is_pid_alive

    with patch("os.kill", side_effect=ProcessLookupError):
        assert _posix_is_pid_alive(1) is False


def test_is_pid_alive_dispatches_to_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """When sys.platform is 'win32' the windows implementation runs."""
    import exlab_wizard.window.main as window_main_module

    monkeypatch.setattr(window_main_module.sys, "platform", "win32")
    monkeypatch.setattr(window_main_module, "_win_is_pid_alive", lambda _pid: True)
    assert is_pid_alive(99) is True
