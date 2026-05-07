"""Tests for :mod:`exlab_wizard.tray.server_runner`. Backend Spec §4.3.2."""

from __future__ import annotations

import json
import os
import socket
import threading
from pathlib import Path
from typing import Any

import pytest

from exlab_wizard.tray.server_runner import (
    SERVER_STATE_FILE,
    ServerRunner,
    pick_free_port,
)


class _FakeUvicornServer:
    """Fake :class:`uvicorn.Server` that runs a tiny event loop."""

    def __init__(self, *, fail: bool = False) -> None:
        self.should_exit = False
        self.run_called = False
        self.run_completed = threading.Event()
        self._fail = fail

    def run(self) -> None:
        self.run_called = True
        if self._fail:
            self.run_completed.set()
            msg = "fake uvicorn run error"
            raise RuntimeError(msg)
        # Spin until ``stop`` flips ``should_exit``.
        for _ in range(2000):  # cap so a buggy test cannot hang forever
            if self.should_exit:
                break
            threading.Event().wait(0.005)
        self.run_completed.set()


def _make_runner(
    tmp_path: Path,
    *,
    fail: bool = False,
) -> tuple[ServerRunner, _FakeUvicornServer]:
    fake_server = _FakeUvicornServer(fail=fail)

    def _build(port: int) -> tuple[Any, Any]:
        config = type("Cfg", (), {"port": port})()
        return config, fake_server

    runner = ServerRunner(app=object(), state_dir=tmp_path)  # type: ignore[arg-type]
    runner._build_uvicorn = _build  # type: ignore[method-assign]
    return runner, fake_server


def test_pick_free_port_returns_bindable_port() -> None:
    port = pick_free_port()
    assert isinstance(port, int)
    assert 0 < port < 65536
    # The returned port should be re-bindable -- we don't hold a socket.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
    finally:
        sock.close()


def test_start_writes_server_json_atomically(tmp_path: Path) -> None:
    runner, fake = _make_runner(tmp_path)
    port = runner.start()
    try:
        state_path = tmp_path / SERVER_STATE_FILE
        assert state_path.exists()
        data = json.loads(state_path.read_text())
        assert data["port"] == port
        assert data["pid"] == os.getpid()
        assert "started_at" in data
        # The atomic write removes its tempfile.
        assert not (tmp_path / "server.json.tmp").exists()
        assert fake.run_called or runner.is_running
    finally:
        runner.stop()


def test_stop_deletes_state_file(tmp_path: Path) -> None:
    runner, _fake = _make_runner(tmp_path)
    runner.start()
    runner.stop()
    state_path = tmp_path / SERVER_STATE_FILE
    assert not state_path.exists()
    assert runner.is_running is False


def test_double_start_raises(tmp_path: Path) -> None:
    runner, _fake = _make_runner(tmp_path)
    runner.start()
    try:
        with pytest.raises(RuntimeError, match="already running"):
            runner.start()
    finally:
        runner.stop()


def test_port_property_before_start_raises(tmp_path: Path) -> None:
    runner, _fake = _make_runner(tmp_path)
    with pytest.raises(RuntimeError, match="before start"):
        _ = runner.port


def test_port_property_after_start(tmp_path: Path) -> None:
    runner, _fake = _make_runner(tmp_path)
    port = runner.start()
    try:
        assert runner.port == port
    finally:
        runner.stop()


def test_state_file_property_returns_expected_path(tmp_path: Path) -> None:
    runner, _fake = _make_runner(tmp_path)
    assert runner.state_file == tmp_path / SERVER_STATE_FILE


def test_stop_is_idempotent(tmp_path: Path) -> None:
    runner, _fake = _make_runner(tmp_path)
    runner.stop()
    runner.stop()
    assert runner.is_running is False


def test_start_creates_state_dir_if_missing(tmp_path: Path) -> None:
    nested = tmp_path / "deep" / "nested"
    runner, _fake = _make_runner(nested)
    runner.start()
    try:
        assert (nested / SERVER_STATE_FILE).exists()
    finally:
        runner.stop()


def test_is_running_tracks_thread_lifecycle(tmp_path: Path) -> None:
    runner, fake = _make_runner(tmp_path)
    assert runner.is_running is False
    runner.start()
    try:
        assert runner.is_running is True
        assert fake.run_called or runner.is_running
    finally:
        runner.stop()
    assert runner.is_running is False


def test_real_uvicorn_resolves_lazily(tmp_path: Path) -> None:
    """The real ``_build_uvicorn`` defers the uvicorn import.

    We can call ``_build_uvicorn`` directly without starting the
    thread; the produced ``uvicorn.Server`` carries the right config.
    """

    runner = ServerRunner(app=object(), state_dir=tmp_path)  # type: ignore[arg-type]
    config, server = runner._build_uvicorn(0)
    assert hasattr(server, "run")
    assert config.host == "127.0.0.1"  # type: ignore[attr-defined]


def test_delete_state_file_handles_missing(tmp_path: Path) -> None:
    runner = ServerRunner(app=object(), state_dir=tmp_path)  # type: ignore[arg-type]
    # No state file present -- _delete_state_file must be a no-op.
    runner._delete_state_file()


def test_stop_handles_attribute_error_on_should_exit(tmp_path: Path) -> None:
    """A custom server object missing ``should_exit`` is tolerated."""
    runner, _fake = _make_runner(tmp_path)
    runner.start()

    class _Slotted:
        __slots__ = ()

    runner._server = _Slotted()
    runner._thread = None
    runner.stop()
    assert runner.is_running is False
