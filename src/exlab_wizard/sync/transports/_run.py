"""Shared asyncio subprocess helper for transport drivers. Backend Spec §7.1.3.

The rclone driver shells out to an external binary; this helper centralises
the spawn + decode logic so the driver only owns its binary-specific
argument construction and failure classification.

This is a plain spawn helper: it launches ``cmd`` in the inherited
process environment and returns its decoded ``(returncode, stdout,
stderr)``. The rclone.conf NAS-sync migration removed all credential
injection, so there is no per-call ``env`` override, stdin piping, or
log redaction here -- NAS credentials live entirely in the operator's
``rclone.conf``.
"""

from __future__ import annotations

import asyncio
import shlex

from exlab_wizard.logging import get_logger

__all__ = ["run_subprocess"]

_log = get_logger(__name__)


async def run_subprocess(cmd: list[str]) -> tuple[int, str, str]:
    """Launch ``cmd`` and return ``(returncode, stdout, stderr)`` as text.

    The child inherits this process's environment unchanged.

    Raises :class:`FileNotFoundError` when the binary named in ``cmd[0]``
    is not on PATH; callers convert this to a
    :class:`~exlab_wizard.sync.transports.TransportError`.
    """
    _log.debug("subprocess cmd: %s", shlex.join(cmd))

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_b, stderr_b = await proc.communicate()
    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")
    rc = proc.returncode if proc.returncode is not None else -1
    return rc, stdout, stderr
