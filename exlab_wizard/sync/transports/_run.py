"""Shared asyncio subprocess helper for transport drivers. Backend Spec §7.1.3.

Both :mod:`rclone` and :mod:`rsync_ssh` shell out to an external binary
and share identical subprocess-spawn + decode logic. This helper
centralises that repeated block so each transport module only contains
its binary-specific argument construction and failure classification.
"""

from __future__ import annotations

import asyncio

__all__ = ["run_subprocess"]


async def run_subprocess(cmd: list[str]) -> tuple[int, str, str]:
    """Launch ``cmd`` via ``asyncio.create_subprocess_exec`` and return
    ``(returncode, stdout, stderr)`` as strings decoded via UTF-8.

    Raises ``FileNotFoundError`` when the binary named in ``cmd[0]`` is
    not found; callers are expected to convert this to a
    :class:`~exlab_wizard.sync.transports.TransportError`.
    """
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
