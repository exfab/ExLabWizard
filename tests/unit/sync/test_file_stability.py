"""Unit tests for the file-stability pre-sync guard."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from exlab_wizard.sync.file_stability import is_stable, wait_until_stable


def test_stable_file_returns_true(tmp_path: Path) -> None:
    f = tmp_path / "done.bin"
    f.write_bytes(b"complete")
    assert is_stable(f, interval=0.01, checks=3, timeout=1.0) is True


def test_unstable_file_returns_false(tmp_path: Path) -> None:
    f = tmp_path / "growing.bin"
    f.write_bytes(b"a")

    stop = threading.Event()

    def _grow() -> None:
        n = 0
        while not stop.is_set():
            n += 1
            f.write_bytes(b"a" * n)
            time.sleep(0.005)

    writer = threading.Thread(target=_grow)
    writer.start()
    try:
        assert is_stable(f, interval=0.01, checks=4, timeout=0.3) is False
    finally:
        stop.set()
        writer.join()


def test_file_disappears_mid_poll_returns_false(tmp_path: Path) -> None:
    f = tmp_path / "vanishes.bin"
    f.write_bytes(b"x" * 10)

    def _delete() -> None:
        time.sleep(0.02)
        f.unlink()

    deleter = threading.Thread(target=_delete)
    deleter.start()
    try:
        assert is_stable(f, interval=0.05, checks=5, timeout=2.0) is False
    finally:
        deleter.join()


def test_timeout_before_stability_returns_false(tmp_path: Path) -> None:
    f = tmp_path / "slow.bin"
    f.write_bytes(b"x")

    stop = threading.Event()

    def _grow() -> None:
        n = 1
        while not stop.is_set():
            n += 1
            f.write_bytes(b"x" * n)
            time.sleep(0.01)

    writer = threading.Thread(target=_grow)
    writer.start()
    try:
        # interval*checks would need > timeout of constant size; never settles.
        assert is_stable(f, interval=0.02, checks=10, timeout=0.1) is False
    finally:
        stop.set()
        writer.join()


def test_empty_file_is_stable(tmp_path: Path) -> None:
    f = tmp_path / "empty.bin"
    f.touch()
    assert is_stable(f, interval=0.01, checks=3, timeout=1.0) is True


def test_missing_file_returns_false_without_raising(tmp_path: Path) -> None:
    assert is_stable(tmp_path / "nope.bin", interval=0.01, checks=2, timeout=0.5) is False


@pytest.mark.parametrize(
    ("interval", "checks"),
    [(0.0, 3), (-1.0, 3), (1.0, 1), (1.0, 0)],
)
def test_invalid_args_raise_value_error(tmp_path: Path, interval: float, checks: int) -> None:
    with pytest.raises(ValueError):
        is_stable(tmp_path / "x", interval=interval, checks=checks, timeout=1.0)


def test_wait_until_stable_partitions(tmp_path: Path) -> None:
    good = tmp_path / "good.bin"
    good.write_bytes(b"done")
    missing = tmp_path / "missing.bin"  # never created -> unstable
    stable, unstable = wait_until_stable(
        [good, missing], interval=0.01, checks=3, timeout=0.5, max_workers=4
    )
    assert stable == [good]
    assert unstable == [missing]


def test_wait_until_stable_empty_input(tmp_path: Path) -> None:
    assert wait_until_stable([], interval=0.01, checks=2, timeout=0.5) == ([], [])


def test_wait_until_stable_logs_debug(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    f = tmp_path / "done.bin"
    f.write_bytes(b"done")
    with caplog.at_level("DEBUG", logger="exlab_wizard.sync.file_stability"):
        wait_until_stable([f], interval=0.01, checks=2, timeout=0.5)
    assert any("stable" in r.message for r in caplog.records)
