"""Tests for ``exlab_wizard.io.atomic_write``."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from exlab_wizard.io.atomic_write import atomic_write_bytes


def test_atomic_write_creates_file(tmp_path: Path) -> None:
    target = tmp_path / "out.json"
    atomic_write_bytes(target, b'{"hello": "world"}')
    assert target.read_bytes() == b'{"hello": "world"}'


def test_atomic_write_overwrites_existing(tmp_path: Path) -> None:
    target = tmp_path / "out.json"
    target.write_bytes(b"old")
    atomic_write_bytes(target, b"new")
    assert target.read_bytes() == b"new"


def test_atomic_write_leaves_no_temp_file_on_success(tmp_path: Path) -> None:
    target = tmp_path / "out.json"
    atomic_write_bytes(target, b"payload")
    leftover = [p for p in tmp_path.iterdir() if p.name != "out.json"]
    assert leftover == [], f"temp files left behind: {leftover}"


def test_atomic_write_cleans_up_temp_on_replace_failure(tmp_path: Path) -> None:
    target = tmp_path / "out.json"

    boom = OSError("disk full")
    with (
        patch("exlab_wizard.io.atomic_write.os.replace", side_effect=boom),
        pytest.raises(OSError, match="disk full"),
    ):
        atomic_write_bytes(target, b"payload")

    assert not target.exists()
    leftover = list(tmp_path.iterdir())
    assert leftover == [], f"temp file leaked after failure: {leftover}"


def test_atomic_write_skip_fsync(tmp_path: Path) -> None:
    target = tmp_path / "out.json"
    with patch("exlab_wizard.io.atomic_write.os.fsync") as fsync_mock:
        atomic_write_bytes(target, b"payload", fsync=False)
    assert fsync_mock.call_count == 0
    assert target.read_bytes() == b"payload"


def test_atomic_write_calls_fsync_by_default(tmp_path: Path) -> None:
    target = tmp_path / "out.json"
    with patch("exlab_wizard.io.atomic_write.os.fsync") as fsync_mock:
        atomic_write_bytes(target, b"payload")
    assert fsync_mock.call_count == 1
