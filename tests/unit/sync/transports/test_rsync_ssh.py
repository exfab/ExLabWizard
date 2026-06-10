"""Unit tests for the rsync-over-ssh transport driver.

rsync-over-ssh NAS transport design (2026-06-10). ``run_subprocess`` is
monkeypatched so no real binary is spawned; the docker integration leg
covers the real wire path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from exlab_wizard.sync.transports import TransportError, TransportErrorKind
from exlab_wizard.sync.transports.rsync_ssh import (
    RsyncSshDriver,
    _classify_failure,
)

TARGET = "svc-sync@nas01:/volume1/lab/EQ1/Run_1"


class FakeRun:
    """Recordable stand-in for ``run_subprocess``."""

    def __init__(self, rc: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.rc, self.stdout, self.stderr = rc, stdout, stderr
        self.cmds: list[list[str]] = []

    async def __call__(self, cmd: list[str]) -> tuple[int, str, str]:
        self.cmds.append(cmd)
        return self.rc, self.stdout, self.stderr


@pytest.fixture
def fake_run(monkeypatch):
    def _install(rc: int = 0, stdout: str = "", stderr: str = "") -> FakeRun:
        fake = FakeRun(rc, stdout, stderr)
        monkeypatch.setattr(
            "exlab_wizard.sync.transports.rsync_ssh.run_subprocess", fake
        )
        return fake

    return _install


class TestClassifyFailure:
    @pytest.mark.parametrize(
        "stderr",
        [
            "svc-sync@nas01: Permission denied (publickey,password).",
            "Host key verification failed.",
            "Too many authentication failures",
        ],
    )
    def test_auth_markers(self, stderr: str) -> None:
        assert _classify_failure(stderr, 255) == TransportErrorKind.AUTH

    def test_ssh_failure_without_auth_marker_is_network(self) -> None:
        assert (
            _classify_failure("ssh: connect to host nas01: Connection refused", 255)
            == TransportErrorKind.NETWORK
        )

    @pytest.mark.parametrize("rc", [5, 10, 12, 23, 24, 30, 35])
    def test_protocol_and_partial_codes_are_network(self, rc: int) -> None:
        assert _classify_failure("", rc) == TransportErrorKind.NETWORK

    def test_other_nonzero_is_unknown(self) -> None:
        assert _classify_failure("rsync: syntax error", 1) == TransportErrorKind.UNKNOWN


class TestPush:
    async def test_success_argv_shape(self, fake_run, tmp_path: Path) -> None:
        run = fake_run(rc=0)
        driver = RsyncSshDriver(ssh_port=2222, ssh_identity_file="/keys/id_exlab")
        result = await driver.push(tmp_path, TARGET, bwlimit_kibps=512)
        assert result.ok
        cmd = run.cmds[0]
        assert cmd[0] == "rsync"
        assert "-rt" in cmd
        assert "--bwlimit=512" in cmd
        assert cmd[-2] == f"{tmp_path}/"
        assert cmd[-1] == TARGET
        ssh_arg = cmd[cmd.index("-e") + 1]
        assert "-p 2222" in ssh_arg
        assert "-i /keys/id_exlab" in ssh_arg
        assert "BatchMode=yes" in ssh_arg

    async def test_files_from_forwarded(self, fake_run, tmp_path: Path) -> None:
        run = fake_run(rc=0)
        listing = tmp_path / "files.txt"
        listing.write_text("a.csv\n", encoding="utf-8")
        await RsyncSshDriver().push(tmp_path, TARGET, files_from=listing)
        assert f"--files-from={listing}" in run.cmds[0]

    async def test_no_bwlimit_flag_when_unset(self, fake_run, tmp_path: Path) -> None:
        run = fake_run(rc=0)
        await RsyncSshDriver().push(tmp_path, TARGET)
        assert not any(arg.startswith("--bwlimit") for arg in run.cmds[0])

    async def test_auth_failure_classified(self, fake_run, tmp_path: Path) -> None:
        fake_run(rc=255, stderr="Permission denied (publickey).")
        result = await RsyncSshDriver().push(tmp_path, TARGET)
        assert not result.ok
        assert result.error_kind == TransportErrorKind.AUTH

    async def test_missing_binary_raises_transport_error(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        async def boom(cmd):
            raise FileNotFoundError(cmd[0])

        monkeypatch.setattr(
            "exlab_wizard.sync.transports.rsync_ssh.run_subprocess", boom
        )
        with pytest.raises(TransportError) as excinfo:
            await RsyncSshDriver().push(tmp_path, TARGET)
        assert excinfo.value.error_kind is None
