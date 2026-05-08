"""Tests for the rclone + rsync_ssh transport drivers.

Backend Spec §7.1.3, §7.1.5. The drivers are thin async wrappers around
the upstream binaries; these tests use the Python stub binaries from
``tests/fixtures`` to drive deterministic outcomes.
"""

from __future__ import annotations

import json
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
    """Install all stub binaries under ``tmp_path/bin`` and prepend to PATH.

    The stub Python scripts already have ``+x`` permissions in the repo;
    we copy them to the per-test bin dir under their canonical command
    names (``rclone``, ``rsync``, and ``ssh`` -- the rsync_ssh hashsum
    path spawns ``ssh`` directly, so the same stub_rsync script is also
    installed as ``ssh`` and dispatches on its argv[0] basename).
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fixtures = Path(__file__).parent.parent.parent / "fixtures"
    rclone_target = bin_dir / "rclone"
    rsync_target = bin_dir / "rsync"
    ssh_target = bin_dir / "ssh"
    shutil.copy(fixtures / "stub_rclone.py", rclone_target)
    shutil.copy(fixtures / "stub_rsync.py", rsync_target)
    shutil.copy(fixtures / "stub_rsync.py", ssh_target)
    for f in (rclone_target, rsync_target, ssh_target):
        st = f.stat()
        f.chmod(st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    return bin_dir


@pytest.fixture()
def record_argv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Tell both stubs to append every invocation's argv to a log file.

    Tests read ``[json.loads(line) for line in record.read_text().splitlines()]``
    to assert exact argv shapes for the rclone and rsync_ssh drivers.
    """
    record = tmp_path / "argv.log"
    monkeypatch.setenv("STUB_RCLONE_RECORD_PATH", str(record))
    monkeypatch.setenv("STUB_RSYNC_RECORD_PATH", str(record))
    return record


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


# ---------------------------------------------------------------------------
# hashsum tests
# ---------------------------------------------------------------------------


async def test_rclone_hashsum_parses_output(
    stub_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stub emits a two-line manifest; the driver parses it into a dict."""
    manifest_text = "deadbeef  a.txt\ncafebabe  data/b.bin\n"
    manifest_file = tmp_path / "manifest.txt"
    manifest_file.write_text(manifest_text)
    monkeypatch.setenv("STUB_RCLONE_BEHAVIOR", "hashsum_success")
    monkeypatch.setenv("STUB_RCLONE_HASHSUM_PATH", str(manifest_file))
    transport = RcloneTransport()
    result = await transport.hashsum("remote:/srv/run")
    assert result == {"a.txt": "deadbeef", "data/b.bin": "cafebabe"}


async def test_rclone_hashsum_raises_transport_error_on_auth(
    stub_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec §7.1.4 step 2 + §7.1.5: an AUTH failure on the hashsum probe
    must surface as :class:`TransportError` carrying ``error_kind=AUTH``.

    Returning an empty dict would silently bypass the §7.1.4 step-2
    remote SHA-256 walk and let the verifier promote the job through
    VERIFIED on the strength of the local-only pass. The queue worker
    needs the classified ``error_kind`` so it can mark the job terminal
    FAILED per the §7.1.5 AUTH row, distinct from a HASH_MISMATCH single
    retry.
    """
    monkeypatch.setenv("STUB_RCLONE_BEHAVIOR", "auth_error")
    transport = RcloneTransport()
    with pytest.raises(TransportError) as excinfo:
        await transport.hashsum("remote:/srv/run")
    assert excinfo.value.error_kind is TransportErrorKind.AUTH


async def test_rclone_hashsum_missing_binary_raises_transport_error(
    tmp_path: Path,
) -> None:
    """A missing binary on the hashsum path raises :class:`TransportError`."""
    transport = RcloneTransport(binary="rclone-not-installed-12345")
    with pytest.raises(TransportError):
        await transport.hashsum("remote:/srv/run")


async def test_rsync_hashsum_parses_output(
    stub_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stub ssh emits a manifest; the driver strips the remote_path prefix."""
    manifest_text = "deadbeef  /srv/nas/myrun/a.txt\ncafebabe  /srv/nas/myrun/data/b.bin\n"
    manifest_file = tmp_path / "manifest.txt"
    manifest_file.write_text(manifest_text)
    monkeypatch.setenv("STUB_RSYNC_BEHAVIOR", "hashsum_success")
    monkeypatch.setenv("STUB_RSYNC_HASHSUM_PATH", str(manifest_file))
    transport = RsyncSshTransport()
    key = tmp_path / "id_ed25519"
    key.write_bytes(b"FAKE-KEY")
    result = await transport.hashsum("user@host", key, "/srv/nas/myrun")
    assert result == {"a.txt": "deadbeef", "data/b.bin": "cafebabe"}


async def test_rsync_hashsum_raises_transport_error_on_network(
    stub_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec §7.1.4 step 2 + §7.1.5: a NETWORK failure on the rsync_ssh
    hashsum probe must surface as :class:`TransportError` carrying
    ``error_kind=NETWORK``.

    Returning an empty dict would silently bypass the §7.1.4 step-2
    remote SHA-256 walk and let the verifier promote the job through
    VERIFIED on the strength of the local-only pass. The queue worker
    needs the classified ``error_kind`` so it can route the failure
    through the §7.1.5 NETWORK exponential-backoff retry path, distinct
    from a HASH_MISMATCH single retry.
    """
    monkeypatch.setenv("STUB_RSYNC_BEHAVIOR", "network_error")
    transport = RsyncSshTransport()
    key = tmp_path / "id_ed25519"
    key.write_bytes(b"k")
    with pytest.raises(TransportError) as excinfo:
        await transport.hashsum("user@host", key, "/srv/nas/myrun")
    assert excinfo.value.error_kind is TransportErrorKind.NETWORK


# ---------------------------------------------------------------------------
# argv-shape tests
# ---------------------------------------------------------------------------


def _read_recorded_argvs(record: Path) -> list[list[str]]:
    """Return the recorded argv lists, in invocation order."""
    return [json.loads(line) for line in record.read_text().splitlines()]


async def test_rclone_argv_includes_checksum_flag(
    stub_dir: Path,
    record_argv: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The push argv always carries ``--checksum`` (per Backend Spec §7.1.3)."""
    src = tmp_path / "src"
    src.mkdir()
    monkeypatch.setenv("STUB_RCLONE_BEHAVIOR", "success")
    transport = RcloneTransport()
    await transport.push(src, "remote:/srv/run")
    argvs = _read_recorded_argvs(record_argv)
    assert argvs, "stub did not record any invocations"
    assert "--checksum" in argvs[0]


async def test_rclone_argv_includes_bwlimit_when_set(
    stub_dir: Path,
    record_argv: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``bwlimit_kibps=512`` -> ``--bwlimit`` immediately followed by ``512K``."""
    src = tmp_path / "src"
    src.mkdir()
    monkeypatch.setenv("STUB_RCLONE_BEHAVIOR", "success")
    transport = RcloneTransport()
    await transport.push(src, "remote:/srv/run", bwlimit_kibps=512)
    argv = _read_recorded_argvs(record_argv)[0]
    assert "--bwlimit" in argv
    idx = argv.index("--bwlimit")
    assert argv[idx + 1] == "512K"


async def test_rclone_argv_omits_bwlimit_when_none(
    stub_dir: Path,
    record_argv: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``bwlimit_kibps=None`` -> ``--bwlimit`` is absent from argv."""
    src = tmp_path / "src"
    src.mkdir()
    monkeypatch.setenv("STUB_RCLONE_BEHAVIOR", "success")
    transport = RcloneTransport()
    await transport.push(src, "remote:/srv/run", bwlimit_kibps=None)
    argv = _read_recorded_argvs(record_argv)[0]
    assert "--bwlimit" not in argv


async def test_rsync_argv_includes_checksum_partial(
    stub_dir: Path,
    record_argv: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The push argv always carries both ``--checksum`` and ``--partial``."""
    src = tmp_path / "src"
    src.mkdir()
    monkeypatch.setenv("STUB_RSYNC_BEHAVIOR", "success")
    transport = RsyncSshTransport()
    key = tmp_path / "id_ed25519"
    key.write_bytes(b"k")
    await transport.push(src, "user@host", key, "/srv/run")
    argv = _read_recorded_argvs(record_argv)[0]
    assert {"--checksum", "--partial"} <= set(argv)


async def test_rsync_argv_includes_ssh_dash_e_with_key_and_batchmode(
    stub_dir: Path,
    record_argv: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ``-e`` value carries ``ssh -i <key>`` and ``-o BatchMode=yes``."""
    src = tmp_path / "src"
    src.mkdir()
    monkeypatch.setenv("STUB_RSYNC_BEHAVIOR", "success")
    transport = RsyncSshTransport()
    key = tmp_path / "id_ed25519"
    key.write_bytes(b"k")
    await transport.push(src, "user@host", key, "/srv/run")
    argv = _read_recorded_argvs(record_argv)[0]
    assert "-e" in argv
    e_value = argv[argv.index("-e") + 1]
    assert "ssh -i" in e_value
    assert str(key) in e_value
    assert "-o BatchMode=yes" in e_value


async def test_rsync_argv_passes_target_and_remote_path(
    stub_dir: Path,
    record_argv: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The final positional arg is exactly ``<ssh_target>:<remote_path>``."""
    src = tmp_path / "src"
    src.mkdir()
    monkeypatch.setenv("STUB_RSYNC_BEHAVIOR", "success")
    transport = RsyncSshTransport()
    key = tmp_path / "id_ed25519"
    key.write_bytes(b"k")
    ssh_target = "user@host"
    remote_path = "/srv/nas/myrun"
    await transport.push(src, ssh_target, key, remote_path)
    argv = _read_recorded_argvs(record_argv)[0]
    assert argv[-1] == f"{ssh_target}:{remote_path}"


async def test_rsync_argv_includes_bwlimit_equals_form_when_set(
    stub_dir: Path,
    record_argv: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spec gap fix: §7.1.7 mandates ``--bwlimit=<K>`` (equals form) for rsync.

    The rclone path uses the space-separated ``--bwlimit <K>`` form; the
    rsync path uses the equals form. The unit-suffix-less integer is what
    the spec spells (``K = upload_mbps * 1024 / 8`` KiB/s, no trailing
    ``K`` in the rsync invocation). This test was missing in the initial
    transport-argv coverage; see Backend Spec §7.1.7.
    """
    src = tmp_path / "src"
    src.mkdir()
    monkeypatch.setenv("STUB_RSYNC_BEHAVIOR", "success")
    transport = RsyncSshTransport()
    key = tmp_path / "id_ed25519"
    key.write_bytes(b"k")
    await transport.push(src, "user@host", key, "/srv/run", bwlimit_kibps=128)
    argv = _read_recorded_argvs(record_argv)[0]
    # Spec form: "--bwlimit=<K>" as a single argv token.
    assert "--bwlimit=128" in argv
    # And the space-separated rclone form must NOT appear.
    assert "--bwlimit" not in [a for a in argv if a == "--bwlimit"]


async def test_rsync_argv_omits_bwlimit_when_none(
    stub_dir: Path,
    record_argv: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spec §7.1.7: ``upload_mbps`` ``null or absent disables limiting``."""
    src = tmp_path / "src"
    src.mkdir()
    monkeypatch.setenv("STUB_RSYNC_BEHAVIOR", "success")
    transport = RsyncSshTransport()
    key = tmp_path / "id_ed25519"
    key.write_bytes(b"k")
    await transport.push(src, "user@host", key, "/srv/run", bwlimit_kibps=None)
    argv = _read_recorded_argvs(record_argv)[0]
    assert not any(a.startswith("--bwlimit") for a in argv)


async def test_rsync_hashsum_argv_uses_find_sha256sum_form(
    stub_dir: Path,
    record_argv: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spec §7.1.4 step 2: rsync remote walk uses ``ssh <target> "find ... -exec sha256sum {} +"``.

    The remote-side hashsum command must use ``find ... -exec sha256sum {} +``
    (literal wording in §7.1.4 step 2) so the verifier sees a manifest in
    the standard ``<hex>  <path>`` form. This argv-shape test was missing
    from the initial coverage.
    """
    monkeypatch.setenv("STUB_RSYNC_BEHAVIOR", "hashsum_success")
    transport = RsyncSshTransport()
    key = tmp_path / "id_ed25519"
    key.write_bytes(b"k")
    await transport.hashsum("user@host", key, "/srv/nas/myrun")
    argv = _read_recorded_argvs(record_argv)[0]
    # The remote command lives as the trailing argv token.
    remote_cmd = argv[-1]
    assert "find " in remote_cmd
    assert "-type f" in remote_cmd
    assert "-exec sha256sum {} +" in remote_cmd
    # The target path appears literally in the find argument.
    assert "/srv/nas/myrun" in remote_cmd
