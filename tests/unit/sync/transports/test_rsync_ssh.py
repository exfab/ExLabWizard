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
        # ``--`` ends option parsing so positionals can never smuggle flags.
        assert cmd[-3] == "--"
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


def _files_from(tmp_path: Path, names: list[str]) -> Path:
    listing = tmp_path / "files-from.txt"
    listing.write_text("".join(f"{n}\n" for n in names), encoding="utf-8")
    return listing


class TestCheck:
    async def test_all_equal_when_nothing_itemized(self, fake_run, tmp_path: Path) -> None:
        run = fake_run(rc=0, stdout="")
        listing = _files_from(tmp_path, ["a.csv", "sub/b.csv"])
        result = await RsyncSshDriver().check(tmp_path, TARGET, files_from=listing)
        assert set(result.equal) == {"a.csv", "sub/b.csv"}
        assert result.differ == () and result.missing_on_dst == ()
        cmd = run.cmds[0]
        assert "--checksum" in cmd
        assert "-rni" in cmd  # dry-run + itemize

    async def test_checksum_differ_flag(self, fake_run, tmp_path: Path) -> None:
        fake_run(rc=0, stdout=">fcst...... a.csv\n")
        listing = _files_from(tmp_path, ["a.csv", "b.csv"])
        result = await RsyncSshDriver().check(tmp_path, TARGET, files_from=listing)
        assert result.differ == ("a.csv",)
        assert result.equal == ("b.csv",)

    async def test_all_plus_means_missing_on_dst(self, fake_run, tmp_path: Path) -> None:
        fake_run(rc=0, stdout=">f+++++++++ new file.csv\n")
        listing = _files_from(tmp_path, ["new file.csv"])
        result = await RsyncSshDriver().check(tmp_path, TARGET, files_from=listing)
        assert result.missing_on_dst == ("new file.csv",)
        assert result.equal == ()

    async def test_short_openrsync_flags_handled(self, fake_run, tmp_path: Path) -> None:
        # openrsync (macOS dev machines) emits 7-char itemize tails.
        fake_run(rc=0, stdout=">f+++++++ x.csv\n")
        listing = _files_from(tmp_path, ["x.csv"])
        result = await RsyncSshDriver().check(tmp_path, TARGET, files_from=listing)
        assert result.missing_on_dst == ("x.csv",)

    async def test_attr_only_lines_count_as_equal(self, fake_run, tmp_path: Path) -> None:
        # '.' update-type = no transfer needed; content equal under --checksum.
        fake_run(rc=0, stdout=".f..t...... a.csv\n")
        listing = _files_from(tmp_path, ["a.csv"])
        result = await RsyncSshDriver().check(tmp_path, TARGET, files_from=listing)
        assert result.equal == ("a.csv",)

    async def test_directory_lines_ignored(self, fake_run, tmp_path: Path) -> None:
        fake_run(rc=0, stdout="cd+++++++++ sub/\n>fcst...... sub/b.csv\n")
        listing = _files_from(tmp_path, ["sub/b.csv"])
        result = await RsyncSshDriver().check(tmp_path, TARGET, files_from=listing)
        assert result.differ == ("sub/b.csv",)

    async def test_nonzero_rc_raises_classified(self, fake_run, tmp_path: Path) -> None:
        fake_run(rc=255, stderr="Permission denied (publickey).")
        listing = _files_from(tmp_path, ["a.csv"])
        with pytest.raises(TransportError) as excinfo:
            await RsyncSshDriver().check(tmp_path, TARGET, files_from=listing)
        assert excinfo.value.error_kind == TransportErrorKind.AUTH


class TestLsjsonManifest:
    async def test_listing_parsed_and_prefix_ignored(self, fake_run, tmp_path: Path) -> None:
        from datetime import datetime

        stamp = datetime.fromtimestamp(1_750_000_000).strftime("%Y/%m/%d %H:%M:%S")
        run = fake_run(
            rc=0,
            stdout=(
                f"drwxr-xr-x          4096 {stamp} .\n"
                f"-rw-r--r--          2048 {stamp} data file.csv\n"
            ),
        )
        manifest = await RsyncSshDriver().lsjson_manifest(
            TARGET, strip_prefix="volume1/lab/EQ1/Run_1"
        )
        assert set(manifest.entries) == {"data file.csv"}
        cmd = run.cmds[0]
        assert "--list-only" in cmd and "-r" in cmd and "--no-h" in cmd
        assert cmd[-1] == f"{TARGET}/"

    async def test_failure_raises_classified(self, fake_run) -> None:
        fake_run(rc=255, stderr="ssh: connect to host nas01: Connection refused")
        with pytest.raises(TransportError) as excinfo:
            await RsyncSshDriver().lsjson_manifest(TARGET)
        assert excinfo.value.error_kind == TransportErrorKind.NETWORK


class TestAbout:
    async def test_reachable_returns_ok_empty_info(self, fake_run) -> None:
        run = fake_run(rc=0, stdout="drwxr-xr-x 4096 2026/06/10 00:00:00 .\n")
        result = await RsyncSshDriver().about("svc-sync@nas01:/volume1/lab")
        assert result.ok and result.info == {}
        assert "-r" not in run.cmds[0]  # non-recursive probe

    async def test_missing_base_root_is_config_reason(self, fake_run) -> None:
        fake_run(rc=23, stdout="", stderr='rsync: change_dir "/volume1/lab" failed')
        result = await RsyncSshDriver().about("svc-sync@nas01:/volume1/lab")
        assert not result.ok
        assert result.reason is not None
        assert "base_root" in result.reason

    async def test_auth_failure_reason(self, fake_run) -> None:
        fake_run(rc=255, stderr="Permission denied (publickey).")
        result = await RsyncSshDriver().about("svc-sync@nas01:/volume1/lab")
        assert not result.ok
        assert result.reason is not None and result.reason.startswith("auth")

    async def test_missing_binary_reason(self, monkeypatch) -> None:
        async def boom(cmd):
            raise FileNotFoundError(cmd[0])

        monkeypatch.setattr(
            "exlab_wizard.sync.transports.rsync_ssh.run_subprocess", boom
        )
        result = await RsyncSshDriver().about("svc-sync@nas01:/volume1/lab")
        assert not result.ok and result.reason == "rsync binary not found"
