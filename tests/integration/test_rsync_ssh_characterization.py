"""Characterization tests for the rsync-over-ssh transport leg.

rsync-over-ssh NAS transport (2026-06-10). These tests drive the REAL
``RsyncSshDriver`` (binary "rsync") at a live ``openssh-server`` +
``rsync`` container that mirrors the Synology posture: key-only auth,
no SFTP subsystem, plain ``rsync --server`` over ssh.

Container TZ is pinned to ``America/New_York`` (see docker-compose.yml)
so it differs from the typical developer's local TZ. The timezone
characterization is embedded in ``test_push_list_reconcile_roundtrip``:
if ``--list-only`` formats in the SERVER's timezone the mtime reconcile
will fail (mtime off by a whole-hour multiple); if it formats in the
CLIENT's (local) timezone the test passes. This is the empirical check
of whether ``parse_rsync_listing``'s local-TZ assumption holds on a
real rsync binary with a cross-TZ server.

Gate: tests skip cleanly when docker is unavailable (CI without docker,
offline dev machines). Run manually with::

    docker compose -f tests/docker/docker-compose.yml up -d --build
    uv run --extra test pytest tests/integration/test_rsync_ssh_characterization.py -v
    docker compose -f tests/docker/docker-compose.yml down
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from exlab_wizard.sync.transports import TransportError, TransportErrorKind
from exlab_wizard.sync.transports.rsync_ssh import RsyncSshDriver

# ---------------------------------------------------------------------------
# Constants — match docker-compose.yml / .env defaults
# ---------------------------------------------------------------------------

_DOCKER_DIR = Path(__file__).resolve().parents[2] / "tests" / "docker"
_SSH_HOST = "localhost"
_SSH_HOST_PORT = int(os.environ.get("SFTP_HOST_PORT", "2222"))
_RSYNC_USER = os.environ.get("RSYNC_USER", "rsyncuser")
_KEY_FILE = _DOCKER_DIR / "keys" / "id_exlab"

# Remote data root inside the container (rsyncuser's data dir).
_REMOTE_DATA_ROOT = f"/home/{_RSYNC_USER}/data"


# ---------------------------------------------------------------------------
# Docker gating — skip when the container stack is not up
# ---------------------------------------------------------------------------


def _docker_available() -> bool:
    """Return True if docker is on PATH and the daemon is running."""
    if not shutil.which("docker"):
        return False
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


def _container_healthy(container: str = "exlab-nas-sftp") -> bool:
    """Return True if the named container is running and healthy."""
    try:
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Status}}", container],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0 and result.stdout.strip() == "running"
    except Exception:
        return False


def _wait_for_ssh(
    host: str = _SSH_HOST,
    port: int = _SSH_HOST_PORT,
    retries: int = 30,
    delay: float = 1.0,
) -> bool:
    """Poll until sshd answers a TCP connect (not full auth)."""
    import socket

    for _ in range(retries):
        try:
            with socket.create_connection((host, port), timeout=2):
                return True
        except OSError:
            time.sleep(delay)
    return False


# Session-scoped fixture: bring the stack up once per pytest session and
# yield the connection parameters; tear down after all tests in the module.
@pytest.fixture(scope="session")
def rsync_container():
    """Bring up the docker compose stack and yield SSH connection params.

    Skips the entire session if docker is unavailable or the container
    fails to come up in time. The fixture does NOT call
    ``docker compose down`` — teardown is the caller's responsibility
    so that developers can inspect logs after a failure. The compose
    stack started here will be stopped by the Makefile / CI workflow.
    """
    if not _docker_available():
        pytest.skip("docker daemon is not available")

    # Bring the stack up (build if needed). Use --no-recreate so a
    # second pytest run in the same session is idempotent.
    build_result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(_DOCKER_DIR / "docker-compose.yml"),
            "up",
            "-d",
            "--build",
        ],
        cwd=_DOCKER_DIR,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if build_result.returncode != 0:
        pytest.skip(
            f"docker compose up failed:\n"
            f"stdout: {build_result.stdout}\n"
            f"stderr: {build_result.stderr}"
        )

    # Wait for sshd to answer connections.
    if not _wait_for_ssh():
        pytest.skip(f"sshd on port {_SSH_HOST_PORT} did not become ready in time")

    # Wait for the keypair to be generated by the entrypoint.
    for _ in range(30):
        if _KEY_FILE.exists() and _KEY_FILE.stat().st_size > 0:
            break
        time.sleep(1)
    else:
        pytest.skip(f"keypair not found at {_KEY_FILE} after 30 s")

    yield {
        "host": _SSH_HOST,
        "port": _SSH_HOST_PORT,
        "user": _RSYNC_USER,
        "key_file": str(_KEY_FILE),
        "data_root": _REMOTE_DATA_ROOT,
    }


@pytest.fixture()
def rsync_driver(rsync_container, tmp_path):
    """Build a RsyncSshDriver pointed at the test container.

    Uses a per-test throwaway known_hosts file (test seam: ssh_extra_opts)
    so the developer's real ~/.ssh/known_hosts is never mutated. On first
    connection, StrictHostKeyChecking=accept-new adds the container's
    key to the throwaway file.
    """
    known_hosts = tmp_path / "known_hosts"
    known_hosts.touch()
    driver = RsyncSshDriver(
        binary="rsync",
        ssh_port=rsync_container["port"],
        ssh_identity_file=rsync_container["key_file"],
        # Test seam only — production never sets these.
        ssh_extra_opts=(
            f"UserKnownHostsFile={known_hosts}",
            "StrictHostKeyChecking=accept-new",
        ),
    )
    return driver, rsync_container


@pytest.fixture()
def remote_target(rsync_container, tmp_path):
    """Compose a unique remote target path for this test."""
    test_name = tmp_path.name  # pytest assigns a unique tmp_path per test
    user = rsync_container["user"]
    data_root = rsync_container["data_root"]
    return f"{user}@{rsync_container['host']}:{data_root}/{test_name}"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_push_list_reconcile_roundtrip(rsync_driver, remote_target):
    """Push a run dir, list it back, verify every file matches within 2 s.

    TIMEZONE CHARACTERIZATION: the container's TZ is America/New_York
    (pinned in docker-compose.yml) which differs from UTC and from most
    developer TZs. ``parse_rsync_listing`` treats listing timestamps as
    LOCAL CLIENT TIME (not server time). If rsync's ``--list-only`` output
    is formatted in the CLIENT's timezone (which the spec assumes and the
    unit tests confirm via stub) then ``manifest.matches(tolerance_s=2)``
    will pass here. If it is formatted in the SERVER's TZ the mtime will
    be off by a whole-hour multiple — the test will fail and the team
    must apply the ``nas.remote_tz`` contingency from the spec.
    """
    driver, _params = rsync_driver
    import tempfile

    with tempfile.TemporaryDirectory() as _td:
        local = Path(_td) / "run"
        (local / "images").mkdir(parents=True)
        # File with spaces in name — exercises listing path parsing.
        (local / "file with spaces.tif").write_bytes(b"TIFF" * 100)
        # File in nested subdir.
        (local / "images" / "slice_001.tif").write_bytes(b"SLICEDATA" * 50)

        result = await driver.push(local, remote_target)
        assert result.ok, f"push failed: rc={result.returncode} stderr={result.stderr!r}"

        manifest = await driver.lsjson_manifest(remote_target)

        for rel_path in ("file with spaces.tif", "images/slice_001.tif"):
            assert manifest.has(rel_path), (
                f"manifest missing {rel_path!r}; entries: {list(manifest.entries)}"
            )
            local_stat = (local / rel_path.replace("/", os.sep)).stat()
            ok = manifest.matches(rel_path, local_stat.st_size, local_stat.st_mtime, tolerance_s=2)
            assert ok, (
                f"mtime mismatch for {rel_path!r}: local={local_stat.st_mtime!r}, "
                f"manifest entry={manifest.entries[rel_path]!r}. "
                f"If offset is a whole-hour multiple, the listing is in the SERVER's "
                f"TZ ({os.environ.get('TZ', 'client-local')}) — apply nas.remote_tz contingency."
            )


async def test_check_differ_and_missing(rsync_driver, remote_target, tmp_path):
    """Push two files; corrupt one remotely, delete the other; check() finds both."""
    driver, params = rsync_driver
    local = tmp_path / "run"
    local.mkdir()
    (local / "good.bin").write_bytes(b"GOOD" * 256)
    (local / "corrupt.bin").write_bytes(b"ORIG" * 256)
    (local / "deleted.bin").write_bytes(b"DELETE_ME" * 100)

    result = await driver.push(local, remote_target)
    assert result.ok, f"push failed: rc={result.returncode} stderr={result.stderr!r}"

    # Locate the remote files via the bind-mounted nas-data directory.
    # remote_target is user@host:/home/rsyncuser/data/<test_name>
    # bind mount: tests/docker/nas-data -> /home/rsyncuser/data
    remote_sub = remote_target.split(":")[-1]
    # Strip the leading /home/rsyncuser/data prefix to get the sub-path.
    data_root = params["data_root"]  # /home/rsyncuser/data
    rel_remote = remote_sub[len(data_root):].lstrip("/")
    nas_data = _DOCKER_DIR / "nas-data" / rel_remote

    corrupt_remote = nas_data / "corrupt.bin"
    deleted_remote = nas_data / "deleted.bin"

    # Corrupt the bytes but preserve the file size and mtime so rsync's
    # modtime-only check would not catch it — only --checksum will.
    orig_stat = corrupt_remote.stat()
    corrupt_remote.write_bytes(b"XXXX" * 256)  # same size (1024 bytes), different content
    os.utime(corrupt_remote, (orig_stat.st_atime, orig_stat.st_mtime))

    # Delete the second file.
    deleted_remote.unlink()

    files_from = tmp_path / "files.txt"
    files_from.write_text("corrupt.bin\ndeleted.bin\ngood.bin\n", encoding="utf-8")

    check = await driver.check(local, remote_target, files_from=files_from)

    assert "corrupt.bin" in check.differ, (
        f"expected corrupt.bin in differ; got differ={check.differ!r}"
    )
    assert "deleted.bin" in check.missing_on_dst, (
        f"expected deleted.bin in missing_on_dst; got {check.missing_on_dst!r}"
    )
    assert "good.bin" in check.equal, (
        f"expected good.bin in equal; got equal={check.equal!r}"
    )


async def test_list_only_on_missing_run_dir_raises_network(rsync_driver, remote_target):
    """lsjson_manifest on a nonexistent dir raises TransportError(NETWORK).

    rsync exits 23 when the remote path doesn't exist; our driver maps
    rc 23 to TransportErrorKind.NETWORK (partial-transfer / directory
    missing category).
    """
    driver, _params = rsync_driver
    missing_target = remote_target + "/does_not_exist_at_all_xyz"

    with pytest.raises(TransportError) as excinfo:
        await driver.lsjson_manifest(missing_target)

    assert excinfo.value.error_kind == TransportErrorKind.NETWORK, (
        f"expected NETWORK, got {excinfo.value.error_kind!r}: {excinfo.value}"
    )


async def test_about_ok_and_missing_base_root(rsync_driver, remote_target, tmp_path):
    """about(existing_path) -> ok=True; about(bogus_path) -> reason mentions base_root."""
    driver, params = rsync_driver

    # First push something so the path exists.
    local = tmp_path / "probe"
    local.mkdir()
    (local / "probe.bin").write_bytes(b"x")
    push_result = await driver.push(local, remote_target)
    assert push_result.ok, f"push failed: {push_result.stderr!r}"

    # about() on the now-existing path must return ok=True.
    ok_result = await driver.about(remote_target)
    assert ok_result.ok, (
        f"expected ok=True for existing path; got reason={ok_result.reason!r}"
    )
    assert ok_result.info == {}, f"unexpected info: {ok_result.info!r}"

    # about() on a bogus path must return ok=False with base_root in reason.
    user = params["user"]
    host = params["host"]
    bogus_target = f"{user}@{host}:/this/path/does/not/exist/at/all"
    bad_result = await driver.about(bogus_target)
    assert not bad_result.ok, "expected ok=False for bogus path"
    assert bad_result.reason is not None, "expected a reason string"
    assert "base_root" in bad_result.reason, (
        f"expected 'base_root' in reason; got {bad_result.reason!r}"
    )
