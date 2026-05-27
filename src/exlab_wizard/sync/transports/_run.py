"""Shared asyncio subprocess helper for transport drivers. Backend Spec §7.1.3.

The rclone driver shells out to an external binary; this helper centralises
the spawn + decode logic so the driver only owns its binary-specific
argument construction and failure classification.

Operator-free per-file NAS sync (2026-05-26): the helper accepts an
``env`` dict (merged over ``os.environ`` for the child only) so rclone
backend credentials can be injected via ``RCLONE_CONFIG_<remote>_*``
env vars at subprocess time, and a ``stdin`` payload for piping a
cleartext password to ``rclone obscure -``. ``mask_for_log`` lists env
keys whose values must be redacted from the debug log so an obscured
password never appears in captured output.
"""

from __future__ import annotations

import asyncio
import os
import shlex

from exlab_wizard.logging import get_logger

__all__ = ["run_subprocess"]

_log = get_logger(__name__)


async def run_subprocess(
    cmd: list[str],
    *,
    env: dict[str, str] | None = None,
    stdin: bytes | None = None,
    mask_for_log: tuple[str, ...] = (),
) -> tuple[int, str, str]:
    """Launch ``cmd`` and return ``(returncode, stdout, stderr)`` as text.

    ``env`` is merged onto a copy of ``os.environ`` for the child process;
    callers pass only the keys they want to add or override. ``stdin``,
    when supplied, is fed to the child and its stdin is closed
    immediately afterwards. ``mask_for_log`` names env keys whose values
    are replaced with ``***`` in the debug log line; the actual
    subprocess environment still carries the real values.

    Raises :class:`FileNotFoundError` when the binary named in ``cmd[0]``
    is not on PATH; callers convert this to a
    :class:`~exlab_wizard.sync.transports.TransportError`.
    """
    child_env = None
    if env is not None:
        child_env = {**os.environ, **env}
        # Surface what we're about to run, with sensitive env values
        # redacted. Logging the unmasked dict would defeat the whole
        # point of mask_for_log.
        if mask_for_log:
            redacted = {
                key: ("***" if key in mask_for_log else value) for key, value in env.items()
            }
        else:
            redacted = dict(env)
        _log.debug("subprocess env additions: %r", redacted)
    _log.debug("subprocess cmd: %s", shlex.join(cmd))

    stdin_pipe = asyncio.subprocess.PIPE if stdin is not None else None
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=stdin_pipe,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=child_env,
    )
    stdout_b, stderr_b = await proc.communicate(input=stdin)
    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")
    rc = proc.returncode if proc.returncode is not None else -1
    return rc, stdout, stderr
