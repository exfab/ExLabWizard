"""Unit tests for ``exlab_wizard.sync.transports._run.run_subprocess``.

The rclone-only migration (2026-05-26) extends the subprocess helper to
forward an env dict and a stdin payload, and to redact sensitive env
keys from the debug log line. These tests pin those contracts so a
later refactor cannot accidentally leak a password into the log or
swallow the env injection.
"""

from __future__ import annotations

import logging
import os
import sys

from exlab_wizard.sync.transports._run import run_subprocess


async def test_run_subprocess_returns_rc_and_streams() -> None:
    rc, stdout, stderr = await run_subprocess(
        [sys.executable, "-c", "import sys; sys.stdout.write('hi'); sys.stderr.write('bye')"]
    )
    assert rc == 0
    assert stdout == "hi"
    assert stderr == "bye"


async def test_run_subprocess_forwards_env() -> None:
    """An env-dict entry must surface in the child process' environment."""
    rc, stdout, _ = await run_subprocess(
        [sys.executable, "-c", "import os, sys; sys.stdout.write(os.environ.get('FOO', ''))"],
        env={"FOO": "bar"},
    )
    assert rc == 0
    assert stdout == "bar"


async def test_run_subprocess_preserves_existing_environ() -> None:
    """Caller-supplied env is *merged* over ``os.environ`` -- it does not replace it."""
    # PATH is essentially always set; we use it as a stable probe for "the
    # parent's environ survived".
    probe_key = "PATH"
    assert probe_key in os.environ
    rc, stdout, _ = await run_subprocess(
        [
            sys.executable,
            "-c",
            f"import os, sys; sys.stdout.write(os.environ.get({probe_key!r}, '__MISSING__'))",
        ],
        env={"UNRELATED_KEY_FOR_PROBE": "x"},
    )
    assert rc == 0
    assert stdout != "__MISSING__"


async def test_run_subprocess_pipes_stdin_payload() -> None:
    """A bytes payload is fed to stdin and closed."""
    rc, stdout, _ = await run_subprocess(
        [sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read())"],
        stdin=b"piped-input",
    )
    assert rc == 0
    assert stdout == "piped-input"


async def test_run_subprocess_masks_named_env_in_debug_log(caplog) -> None:
    """``mask_for_log`` redacts the listed env keys from the debug log line."""
    caplog.set_level(logging.DEBUG, logger="exlab_wizard.sync.transports._run")
    rc, _stdout, _stderr = await run_subprocess(
        [sys.executable, "-c", "pass"],
        env={"SECRET_TOKEN": "supersecret-12345", "PUBLIC_FLAG": "ok"},
        mask_for_log=("SECRET_TOKEN",),
    )
    assert rc == 0
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    # The masked key value must NOT appear anywhere in the captured log.
    assert "supersecret-12345" not in log_text
    # The unmasked key keeps its real value so operators can still spot
    # configuration mistakes through the log.
    assert "PUBLIC_FLAG" in log_text
    assert "ok" in log_text
    # And the masked key's identity is still visible (just its value isn't).
    assert "SECRET_TOKEN" in log_text


async def test_run_subprocess_omits_env_log_when_env_is_none(caplog) -> None:
    """No env block in the log when the caller passes ``env=None``."""
    caplog.set_level(logging.DEBUG, logger="exlab_wizard.sync.transports._run")
    await run_subprocess([sys.executable, "-c", "pass"])
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "subprocess env additions" not in log_text
