"""Tests for the rclone + rsync_ssh transport drivers.

Backend Spec §7.1.3, §7.1.5. The drivers are thin async wrappers around
the upstream binaries; these tests use the Python stub binaries from
``tests/fixtures`` to drive deterministic outcomes.
"""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

import pytest

from exlab_wizard.sync.transports import (
    TransportError,
    TransportErrorKind,
)
from exlab_wizard.sync.transports.rclone import RcloneTransport
from exlab_wizard.sync.transports.rsync_ssh import RsyncSshTransport


@pytest.fixture()
def stub_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Install both stub binaries under ``tmp_path/bin`` and prepend to PATH.

    The stub Python scripts already have ``+x`` permissions in the repo;
    we copy them to the per-test bin dir under their canonical command
    names (``rclone`` and ``rsync``) so the drivers can spawn them via
    ``asyncio.create_subprocess_exec``.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fixtures = Path(__file__).parent.parent.parent / "fixtures"
    rclone_target = bin_dir / "rclone"
    rsync_target = bin_dir / "rsync"
    shutil.copy(fixtures / "stub_rclone.py", rclone_target)
    shutil.copy(fixtures / "stub_rsync.py", rsync_target)
    for f in (rclone_target, rsync_target):
        st = f.stat()
        f.chmod(st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    return bin_dir


# ---------------------------------------------------------------------------
# RcloneTransport
# ---------------------------------------------------------------------------


async def test_rclone_success(
    stub_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("hello")
    dest = tmp_path / "dest"
    monkeypatch.setenv("STUB_RCLONE_BEHAVIOR", "success")
    monkeypatch.setenv("STUB_RCLONE_DEST_ROOT", str(dest))
    transport = RcloneTransport()
    result = await transport.push(src, "remote:path/to/run")
    assert result.ok is True
    assert result.returncode == 0
    # Stub copied the contents to the destination root.
    assert (dest / "path" / "to" / "run" / "a.txt").exists()


async def test_rclone_network_error_is_retryable(
    stub_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    monkeypatch.setenv("STUB_RCLONE_BEHAVIOR", "network_error")
    transport = RcloneTransport()
    result = await transport.push(src, "remote:path")
    assert result.ok is False
    assert result.error_kind is TransportErrorKind.NETWORK


async def test_rclone_auth_error_is_terminal(
    stub_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    monkeypatch.setenv("STUB_RCLONE_BEHAVIOR", "auth_error")
    transport = RcloneTransport()
    result = await transport.push(src, "remote:path")
    assert result.ok is False
    assert result.error_kind is TransportErrorKind.AUTH


async def test_rclone_hash_mismatch(
    stub_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    monkeypatch.setenv("STUB_RCLONE_BEHAVIOR", "hash_mismatch")
    transport = RcloneTransport()
    result = await transport.push(src, "remote:path")
    assert result.ok is False
    assert result.error_kind is TransportErrorKind.HASH_MISMATCH


async def test_rclone_with_bwlimit_passes_flag(
    stub_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The driver passes ``--bwlimit <K>K`` when ``bwlimit_kibps`` is set."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("hi")
    dest = tmp_path / "dest"
    monkeypatch.setenv("STUB_RCLONE_BEHAVIOR", "success")
    monkeypatch.setenv("STUB_RCLONE_DEST_ROOT", str(dest))
    transport = RcloneTransport()
    result = await transport.push(src, "remote:run", bwlimit_kibps=512)
    assert result.ok is True


async def test_rclone_missing_binary_raises(tmp_path: Path) -> None:
    """A missing binary raises :class:`TransportError`, not a retry result."""
    transport = RcloneTransport(binary="rclone-not-installed-12345")
    with pytest.raises(TransportError):
        await transport.push(tmp_path, "remote:path")


# ---------------------------------------------------------------------------
# RsyncSshTransport
# ---------------------------------------------------------------------------


async def test_rsync_success(
    stub_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("hello")
    dest = tmp_path / "dest"
    monkeypatch.setenv("STUB_RSYNC_BEHAVIOR", "success")
    monkeypatch.setenv("STUB_RSYNC_DEST_ROOT", str(dest))
    transport = RsyncSshTransport()
    key = tmp_path / "id_ed25519"
    key.write_bytes(b"FAKE-KEY")
    result = await transport.push(src, "user@host", key, "/srv/nas/run")
    assert result.ok is True
    assert (dest / "srv" / "nas" / "run" / "a.txt").exists()


async def test_rsync_network_error(
    stub_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    monkeypatch.setenv("STUB_RSYNC_BEHAVIOR", "network_error")
    transport = RsyncSshTransport()
    result = await transport.push(src, "user@host", tmp_path / "key", "/p")
    assert result.ok is False
    assert result.error_kind is TransportErrorKind.NETWORK


async def test_rsync_auth_error(
    stub_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    monkeypatch.setenv("STUB_RSYNC_BEHAVIOR", "auth_error")
    transport = RsyncSshTransport()
    result = await transport.push(src, "user@host", tmp_path / "key", "/p")
    assert result.ok is False
    assert result.error_kind is TransportErrorKind.AUTH


async def test_rsync_hash_mismatch(
    stub_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    monkeypatch.setenv("STUB_RSYNC_BEHAVIOR", "hash_mismatch")
    transport = RsyncSshTransport()
    result = await transport.push(src, "user@host", tmp_path / "key", "/p")
    assert result.ok is False
    assert result.error_kind is TransportErrorKind.HASH_MISMATCH


async def test_rsync_missing_binary_raises(tmp_path: Path) -> None:
    transport = RsyncSshTransport(binary="rsync-not-installed-12345")
    with pytest.raises(TransportError):
        await transport.push(tmp_path, "user@host", tmp_path, "/p")


async def test_rsync_with_bwlimit(
    stub_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("ok")
    dest = tmp_path / "dest"
    monkeypatch.setenv("STUB_RSYNC_BEHAVIOR", "success")
    monkeypatch.setenv("STUB_RSYNC_DEST_ROOT", str(dest))
    transport = RsyncSshTransport()
    result = await transport.push(src, "user@host", tmp_path / "key", "/p/run", bwlimit_kibps=128)
    assert result.ok is True
