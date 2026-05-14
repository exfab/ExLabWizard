"""Tests for ``exlab_wizard.tray.storage_secret``.

The secret backs NiceGUI's Starlette ``SessionMiddleware``; we only
need to verify that generation is idempotent, that the file is mode
0600, and that a corrupted/empty file regenerates rather than crashes.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

from exlab_wizard.tray.storage_secret import (
    STORAGE_SECRET_FILE,
    load_or_create_storage_secret,
)


def test_generates_secret_on_first_call(tmp_path: Path) -> None:
    secret = load_or_create_storage_secret(tmp_path)
    path = tmp_path / STORAGE_SECRET_FILE
    assert path.exists()
    assert path.read_text(encoding="utf-8").strip() == secret
    assert len(secret) == 64  # 32 bytes hex-encoded


def test_idempotent_across_calls(tmp_path: Path) -> None:
    first = load_or_create_storage_secret(tmp_path)
    second = load_or_create_storage_secret(tmp_path)
    assert first == second


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits only")
def test_secret_file_mode_is_0600(tmp_path: Path) -> None:
    load_or_create_storage_secret(tmp_path)
    path = tmp_path / STORAGE_SECRET_FILE
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600


def test_regenerates_on_empty_file(tmp_path: Path) -> None:
    path = tmp_path / STORAGE_SECRET_FILE
    path.write_text("", encoding="utf-8")
    secret = load_or_create_storage_secret(tmp_path)
    assert secret
    assert path.read_text(encoding="utf-8").strip() == secret


def test_regenerates_on_whitespace_only_file(tmp_path: Path) -> None:
    path = tmp_path / STORAGE_SECRET_FILE
    path.write_text("   \n\t", encoding="utf-8")
    secret = load_or_create_storage_secret(tmp_path)
    assert secret
    assert path.read_text(encoding="utf-8").strip() == secret


def test_creates_state_dir_if_missing(tmp_path: Path) -> None:
    state_dir = tmp_path / "deep" / "nested" / "state"
    secret = load_or_create_storage_secret(state_dir)
    assert (state_dir / STORAGE_SECRET_FILE).exists()
    assert secret
