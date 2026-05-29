"""Unit tests for ``exlab_wizard.sync.transports._run.run_subprocess``.

rclone.conf NAS-sync migration (Phase 7): the helper is a plain spawn
helper -- it launches ``cmd`` in the inherited environment and returns
its decoded ``(returncode, stdout, stderr)``. The credential-injection
(``env`` / ``stdin`` / ``mask_for_log``) plumbing was removed.
"""

from __future__ import annotations

import logging
import sys

from exlab_wizard.sync.transports._run import run_subprocess


async def test_run_subprocess_returns_rc_and_streams() -> None:
    rc, stdout, stderr = await run_subprocess(
        [sys.executable, "-c", "import sys; sys.stdout.write('hi'); sys.stderr.write('bye')"]
    )
    assert rc == 0
    assert stdout == "hi"
    assert stderr == "bye"


async def test_run_subprocess_inherits_environment() -> None:
    """The child inherits the parent's environment unchanged."""
    # PATH is essentially always set; we use it as a stable probe for "the
    # parent's environ survived".
    rc, stdout, _ = await run_subprocess(
        [
            sys.executable,
            "-c",
            "import os, sys; sys.stdout.write(os.environ.get('PATH', '__MISSING__'))",
        ],
    )
    assert rc == 0
    assert stdout != "__MISSING__"


async def test_run_subprocess_logs_only_the_command(caplog) -> None:
    """The debug log carries the command line -- and no env block."""
    caplog.set_level(logging.DEBUG, logger="exlab_wizard.sync.transports._run")
    await run_subprocess([sys.executable, "-c", "pass"])
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "subprocess cmd" in log_text
    assert "subprocess env additions" not in log_text
