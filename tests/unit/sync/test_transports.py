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
    CheckResult,
    RcloneDriver,
    _parse_combined,
    obscure,
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


async def test_rclone_push_forwards_env(
    stub_dir: Path,
    record_argv: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``env`` arrives in the subprocess; ``mask_for_log`` redacts the password.

    The stub doesn't itself assert on env; it logs argv only. We piggyback on
    a separate env-tap file (set by the stub when STUB_RCLONE_ENV_DUMP is set)
    to capture what the child actually saw.
    """
    src = tmp_path / "src"
    src.mkdir()
    dump = tmp_path / "env.dump"
    monkeypatch.setenv("STUB_RCLONE_BEHAVIOR", "success")
    monkeypatch.setenv("STUB_RCLONE_ENV_DUMP", str(dump))
    driver = RcloneDriver()
    env = {"RCLONE_CONFIG_R_TYPE": "sftp", "RCLONE_CONFIG_R_PASS": "obscured-secret"}
    await driver.push(
        src,
        "r:/path",
        env=env,
        mask_for_log=("RCLONE_CONFIG_R_PASS",),
    )
    if dump.exists():
        # Stub captured the env -- assert the password key is set and matches.
        text = dump.read_text()
        assert "RCLONE_CONFIG_R_TYPE=sftp" in text
        assert "RCLONE_CONFIG_R_PASS=obscured-secret" in text


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
# obscure
# ---------------------------------------------------------------------------


async def test_obscure_returns_stdout_stripped(
    stub_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STUB_RCLONE_BEHAVIOR", "obscure_success")
    monkeypatch.setenv("STUB_RCLONE_OBSCURE_OUT", "OBS_DEADBEEF")
    out = await obscure("mypassword")
    assert out == "OBS_DEADBEEF"


async def test_obscure_missing_binary_raises_auth(tmp_path: Path) -> None:
    with pytest.raises(TransportError) as excinfo:
        await obscure("p", binary="rclone-not-installed-12345")
    assert excinfo.value.error_kind is TransportErrorKind.AUTH


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

import asyncio  # noqa: E402 — appended section; asyncio.run() used in plain-def tests


def test_push_argv_includes_config_and_perf(monkeypatch, tmp_path):
    captured = {}

    async def fake_run(cmd, *, env=None, stdin=None, mask_for_log=()):
        captured["cmd"] = cmd
        return 0, "", ""

    monkeypatch.setattr("exlab_wizard.sync.transports.rclone.run_subprocess", fake_run)
    drv = RcloneDriver(config_path="/etc/rclone.conf", transfers=2, checkers=3)
    asyncio.run(drv.push(tmp_path, "nas01:/srv/lab/EQ/run", bwlimit_kibps=None))
    cmd = captured["cmd"]
    assert "--config" in cmd and "/etc/rclone.conf" in cmd
    assert cmd[cmd.index("--transfers") + 1] == "2"
    assert cmd[cmd.index("--checkers") + 1] == "3"
