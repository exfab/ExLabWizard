"""Tests for the rclone transport driver.

Backend Spec §7.1.3, §7.1.5. The driver is a thin async wrapper around
the upstream binary; these tests use the Python stub at
``tests/fixtures/stub_rclone.py`` to drive deterministic outcomes. After
the rclone-only migration (2026-05-26) rsync is gone; there is one
driver, one stub, and one set of behaviors to assert.
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
from exlab_wizard.sync.transports.rclone import (
    AboutResult,
    CheckResult,
    RcloneDriver,
    _classify_failure,
    _parse_combined,
)


@pytest.fixture()
def stub_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Install the rclone stub under ``tmp_path/bin`` and prepend to PATH."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fixtures = Path(__file__).parent.parent.parent / "fixtures"
    rclone_target = bin_dir / "rclone"
    shutil.copy(fixtures / "stub_rclone.py", rclone_target)
    st = rclone_target.stat()
    rclone_target.chmod(st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    return bin_dir


@pytest.fixture()
def record_argv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Tell the stub to append every invocation's argv to a log file."""
    record = tmp_path / "argv.log"
    monkeypatch.setenv("STUB_RCLONE_RECORD_PATH", str(record))
    return record


def _read_recorded_argvs(record: Path) -> list[list[str]]:
    """Return the recorded argv lists, in invocation order."""
    return [json.loads(line) for line in record.read_text().splitlines()]


# ---------------------------------------------------------------------------
# push
# ---------------------------------------------------------------------------


async def test_rclone_push_success(
    stub_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("hello")
    dest = tmp_path / "dest"
    monkeypatch.setenv("STUB_RCLONE_BEHAVIOR", "success")
    monkeypatch.setenv("STUB_RCLONE_DEST_ROOT", str(dest))
    driver = RcloneDriver()
    result = await driver.push(src, "remote:path/to/run")
    assert result.ok is True
    assert result.returncode == 0
    assert (dest / "path" / "to" / "run" / "a.txt").exists()


async def test_rclone_push_network_error_is_retryable(
    stub_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    monkeypatch.setenv("STUB_RCLONE_BEHAVIOR", "network_error")
    driver = RcloneDriver()
    result = await driver.push(src, "remote:path")
    assert result.ok is False
    assert result.error_kind is TransportErrorKind.NETWORK


async def test_rclone_push_auth_error_is_terminal(
    stub_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    monkeypatch.setenv("STUB_RCLONE_BEHAVIOR", "auth_error")
    driver = RcloneDriver()
    result = await driver.push(src, "remote:path")
    assert result.ok is False
    assert result.error_kind is TransportErrorKind.AUTH


async def test_rclone_push_hash_mismatch(
    stub_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    monkeypatch.setenv("STUB_RCLONE_BEHAVIOR", "hash_mismatch")
    driver = RcloneDriver()
    result = await driver.push(src, "remote:path")
    assert result.ok is False
    assert result.error_kind is TransportErrorKind.HASH_MISMATCH


async def test_rclone_push_missing_binary_raises(tmp_path: Path) -> None:
    """A missing binary raises :class:`TransportError`, not a retry result."""
    driver = RcloneDriver(binary="rclone-not-installed-12345")
    with pytest.raises(TransportError):
        await driver.push(tmp_path, "remote:path")


# ---------------------------------------------------------------------------
# argv shape
# ---------------------------------------------------------------------------


async def test_rclone_argv_includes_checksum_flag(
    stub_dir: Path,
    record_argv: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    monkeypatch.setenv("STUB_RCLONE_BEHAVIOR", "success")
    driver = RcloneDriver()
    await driver.push(src, "remote:/srv/run")
    argvs = _read_recorded_argvs(record_argv)
    assert argvs, "stub did not record any invocations"
    assert "--checksum" in argvs[0]


async def test_rclone_argv_includes_bwlimit_when_set(
    stub_dir: Path,
    record_argv: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    monkeypatch.setenv("STUB_RCLONE_BEHAVIOR", "success")
    driver = RcloneDriver()
    await driver.push(src, "remote:/srv/run", bwlimit_kibps=512)
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
    src = tmp_path / "src"
    src.mkdir()
    monkeypatch.setenv("STUB_RCLONE_BEHAVIOR", "success")
    driver = RcloneDriver()
    await driver.push(src, "remote:/srv/run", bwlimit_kibps=None)
    argv = _read_recorded_argvs(record_argv)[0]
    assert "--bwlimit" not in argv


async def test_rclone_argv_includes_files_from_when_set(
    stub_dir: Path,
    record_argv: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    files_from = tmp_path / "files.txt"
    files_from.write_text("data.bin\n")
    monkeypatch.setenv("STUB_RCLONE_BEHAVIOR", "success")
    driver = RcloneDriver()
    await driver.push(src, "remote:/srv/run", files_from=files_from)
    argv = _read_recorded_argvs(record_argv)[0]
    assert "--files-from" in argv
    idx = argv.index("--files-from")
    assert argv[idx + 1] == str(files_from)


async def test_rclone_argv_omits_files_from_when_none(
    stub_dir: Path,
    record_argv: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    monkeypatch.setenv("STUB_RCLONE_BEHAVIOR", "success")
    driver = RcloneDriver()
    await driver.push(src, "remote:/srv/run")
    argv = _read_recorded_argvs(record_argv)[0]
    assert "--files-from" not in argv


# ---------------------------------------------------------------------------
# about
# ---------------------------------------------------------------------------


async def test_about_success(stub_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STUB_RCLONE_BEHAVIOR", "about_success")
    monkeypatch.setenv("STUB_RCLONE_ABOUT_JSON", '{"total": 1024, "used": 512, "free": 512}')
    driver = RcloneDriver()
    result = await driver.about("remote:")
    assert result.ok is True
    assert result.info == {"total": 1024, "used": 512, "free": 512}


async def test_about_auth_failure(stub_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STUB_RCLONE_BEHAVIOR", "about_auth_error")
    driver = RcloneDriver()
    result = await driver.about("remote:")
    assert result.ok is False
    assert result.reason is not None
    assert "auth" in result.reason.lower()


# ---------------------------------------------------------------------------
# _parse_combined (rclone check --combined output parser)
# ---------------------------------------------------------------------------


def test_parse_combined_handles_all_prefixes() -> None:
    text = "= same.txt\n* differ.txt\n+ extra.txt\n- missing.txt\n! error.txt\n"
    result = _parse_combined(text)
    assert result.equal == ("same.txt",)
    assert result.differ == ("differ.txt",)
    assert result.extra_on_dst == ("extra.txt",)
    assert result.missing_on_dst == ("missing.txt",)
    assert result.errors == ("error.txt",)


def test_parse_combined_empty_returns_empty_result() -> None:
    assert _parse_combined("") == CheckResult()


def test_parse_combined_tolerates_blank_lines_and_unknown_prefix() -> None:
    text = "\n= a.txt\n  \n? mysterious.txt\n* b.txt\n"
    result = _parse_combined(text)
    assert result.equal == ("a.txt",)
    assert result.differ == ("b.txt",)


# ---------------------------------------------------------------------------
# Task 2.1 — --config / --transfers / --checkers plumbing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_push_argv_includes_config_and_perf(monkeypatch, tmp_path):
    captured = {}

    async def fake_run(cmd):
        captured["cmd"] = cmd
        return 0, "", ""

    monkeypatch.setattr("exlab_wizard.sync.transports.rclone.run_subprocess", fake_run)
    drv = RcloneDriver(config_path="/etc/rclone.conf", transfers=2, checkers=3)
    await drv.push(tmp_path, "nas01:/srv/lab/EQ/run", bwlimit_kibps=None)
    cmd = captured["cmd"]
    assert "--config" in cmd and "/etc/rclone.conf" in cmd
    assert cmd[cmd.index("--transfers") + 1] == "2"
    assert cmd[cmd.index("--checkers") + 1] == "3"


# ---------------------------------------------------------------------------
# Task 2.2 — RcloneDriver.lsjson
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lsjson_argv_is_recursive_readonly(monkeypatch):
    captured = {}

    async def fake_run(cmd):
        captured["cmd"] = cmd
        return (
            0,
            '[{"Path":"a.txt","Name":"a.txt","Size":3,"ModTime":"2026-05-28T00:00:00Z","IsDir":false}]',
            "",
        )

    monkeypatch.setattr("exlab_wizard.sync.transports.rclone.run_subprocess", fake_run)
    out = await RcloneDriver().lsjson("nas01:/srv/lab/EQ/run")
    assert captured["cmd"][:2] == ["rclone", "lsjson"]
    assert "-R" in captured["cmd"]
    assert "nas01:/srv/lab/EQ/run" in captured["cmd"]
    assert '"Path":"a.txt"' in out


@pytest.mark.asyncio
async def test_lsjson_raises_transport_error_on_failure(monkeypatch):
    async def fake_run(cmd):
        return 1, "", "401 Unauthorized"

    monkeypatch.setattr("exlab_wizard.sync.transports.rclone.run_subprocess", fake_run)
    with pytest.raises(TransportError):
        await RcloneDriver().lsjson("nas01:/x")


# ---------------------------------------------------------------------------
# Task 2.3 — RcloneDriver.listremotes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_listremotes_parses_lines(monkeypatch):
    async def fake_run(cmd):
        assert cmd[:2] == ["rclone", "listremotes"]
        return 0, "nas01:\nstagepc:\n", ""

    monkeypatch.setattr("exlab_wizard.sync.transports.rclone.run_subprocess", fake_run)
    remotes = await RcloneDriver().listremotes()
    assert remotes == ("nas01:", "stagepc:")


@pytest.mark.asyncio
async def test_listremotes_empty_on_failure(monkeypatch):
    async def fake_run(cmd):
        return 1, "", "config not found"

    monkeypatch.setattr("exlab_wizard.sync.transports.rclone.run_subprocess", fake_run)
    assert await RcloneDriver().listremotes() == ()


# ---------------------------------------------------------------------------
# _classify_failure -- the UNKNOWN fallback (rc == 0, no markers)
# ---------------------------------------------------------------------------


def test_classify_failure_unknown_when_rc_zero_and_no_markers() -> None:
    assert _classify_failure("everything fine", 0) is TransportErrorKind.UNKNOWN


# ---------------------------------------------------------------------------
# check -- error paths (missing binary / auth failure / unreadable combined)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_missing_binary_raises(monkeypatch, tmp_path) -> None:
    async def fake_run(cmd):
        raise FileNotFoundError("rclone")

    monkeypatch.setattr("exlab_wizard.sync.transports.rclone.run_subprocess", fake_run)
    files_from = tmp_path / "files.txt"
    files_from.write_text("a.txt\n")
    with pytest.raises(TransportError):
        await RcloneDriver().check(tmp_path, "nas01:/x", files_from=files_from)


@pytest.mark.asyncio
async def test_check_auth_failure_with_no_combined_output_raises(monkeypatch, tmp_path) -> None:
    async def fake_run(cmd):
        # Non-zero exit, auth marker, and the --combined file is left empty.
        return 1, "", "403 Forbidden"

    monkeypatch.setattr("exlab_wizard.sync.transports.rclone.run_subprocess", fake_run)
    files_from = tmp_path / "files.txt"
    files_from.write_text("a.txt\n")
    with pytest.raises(TransportError) as exc:
        await RcloneDriver().check(tmp_path, "nas01:/x", files_from=files_from)
    assert exc.value.error_kind is TransportErrorKind.AUTH


@pytest.mark.asyncio
async def test_check_unreadable_combined_file_is_tolerated(monkeypatch, tmp_path) -> None:
    """If the --combined tempfile cannot be read, treat it as empty output."""

    async def fake_run(cmd):
        # Delete the --combined target so read_text raises OSError; exit clean.
        combined = Path(cmd[cmd.index("--combined") + 1])
        combined.unlink(missing_ok=True)
        return 0, "", ""

    monkeypatch.setattr("exlab_wizard.sync.transports.rclone.run_subprocess", fake_run)
    files_from = tmp_path / "files.txt"
    files_from.write_text("a.txt\n")
    result = await RcloneDriver().check(tmp_path, "nas01:/x", files_from=files_from)
    assert result == CheckResult()


# ---------------------------------------------------------------------------
# about -- error paths (missing binary / malformed JSON)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_about_missing_binary_returns_not_ok(monkeypatch) -> None:
    async def fake_run(cmd):
        raise FileNotFoundError("rclone")

    monkeypatch.setattr("exlab_wizard.sync.transports.rclone.run_subprocess", fake_run)
    result = await RcloneDriver().about("nas01:")
    assert result == AboutResult(ok=False, reason="rclone binary not found")


@pytest.mark.asyncio
async def test_about_malformed_json_is_ok_with_empty_info(monkeypatch) -> None:
    async def fake_run(cmd):
        return 0, "this is not json", ""

    monkeypatch.setattr("exlab_wizard.sync.transports.rclone.run_subprocess", fake_run)
    result = await RcloneDriver().about("nas01:")
    assert result.ok is True
    assert result.info == {}


# ---------------------------------------------------------------------------
# lsjson -- missing binary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lsjson_missing_binary_raises(monkeypatch) -> None:
    async def fake_run(cmd):
        raise FileNotFoundError("rclone")

    monkeypatch.setattr("exlab_wizard.sync.transports.rclone.run_subprocess", fake_run)
    with pytest.raises(TransportError):
        await RcloneDriver().lsjson("nas01:/x")


async def test_rclone_lsjson_manifest_parses_and_strips(monkeypatch) -> None:
    from exlab_wizard.sync.transports.rclone import RcloneDriver

    raw = (
        '[{"Path": "base/EQ1/Run_1/a.csv", "Size": 10,'
        ' "ModTime": "2026-06-10T00:00:00Z", "IsDir": false}]'
    )

    async def fake_lsjson(self, remote, *, recursive=True):
        return raw

    monkeypatch.setattr(RcloneDriver, "lsjson", fake_lsjson)
    manifest = await RcloneDriver().lsjson_manifest(
        "nas01:/base/EQ1/Run_1", strip_prefix="base/EQ1/Run_1"
    )
    assert manifest.has("a.csv")


def test_rclone_driver_satisfies_protocol() -> None:
    from exlab_wizard.sync.transports import NasTransportDriver
    from exlab_wizard.sync.transports.rclone import RcloneDriver

    driver: NasTransportDriver = RcloneDriver()
    assert driver is not None


class TestBuildNasDriver:
    def test_rclone_default(self) -> None:
        from exlab_wizard.config.models import NasConfig, RclonePerf
        from exlab_wizard.sync.transports import build_nas_driver
        from exlab_wizard.sync.transports.rclone import RcloneDriver

        driver = build_nas_driver(NasConfig(remote="nas01"), RclonePerf())
        assert isinstance(driver, RcloneDriver)

    def test_rsync_ssh_selected(self) -> None:
        from exlab_wizard.config.models import NasConfig, RclonePerf
        from exlab_wizard.sync.transports import build_nas_driver
        from exlab_wizard.sync.transports.rsync_ssh import RsyncSshDriver

        nas = NasConfig(
            transport="rsync_ssh",
            remote="svc-sync@nas01",
            ssh_port=2222,
            ssh_identity_file="~/.ssh/id_exlab",
        )
        driver = build_nas_driver(nas, RclonePerf())
        assert isinstance(driver, RsyncSshDriver)
        assert driver._ssh_port == 2222
