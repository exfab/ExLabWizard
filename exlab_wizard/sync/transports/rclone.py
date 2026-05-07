"""rclone transport driver. Backend Spec §7.1.3.

Shells out to ``rclone copy --checksum --bwlimit=<K>K <local> <remote>:<path>``.
The remote name and path live in ``config.yaml``
(``equipment.transport.rclone_remote`` + ``rclone_remote_path``); rclone
itself reads ``rclone.conf`` for the credential map.

The driver is intentionally thin: it calls the binary, captures stdout /
stderr, and translates the exit-code + stderr-substring into one of the
``TransportErrorKind`` retry classes. Hash verification is the
:mod:`exlab_wizard.sync.verifier` module's responsibility, NOT this
driver's.
"""

from __future__ import annotations

import asyncio
import shlex
from pathlib import Path

from exlab_wizard.logging import get_logger
from exlab_wizard.sync.transports import (
    TransportError,
    TransportErrorKind,
    TransportResult,
)

__all__ = ["RcloneTransport"]

_log = get_logger(__name__)


# Substrings that indicate authentication failure rather than a transient
# network error. The match is case-insensitive.
_AUTH_FAILURE_MARKERS: tuple[str, ...] = (
    "auth_error",
    "authentication failed",
    "permission denied",
    "401 unauthorized",
    "403 forbidden",
    "access denied",
)


def _classify_failure(stderr: str, returncode: int) -> TransportErrorKind:
    """Map a (returncode, stderr) into a :class:`TransportErrorKind`.

    Auth failures (``401 / 403 / "permission denied"``) are terminal;
    every other non-zero code is treated as a retryable network error.
    """
    lowered = stderr.lower()
    if any(marker in lowered for marker in _AUTH_FAILURE_MARKERS):
        return TransportErrorKind.AUTH
    if "hash mismatch" in lowered or "checksum mismatch" in lowered:
        return TransportErrorKind.HASH_MISMATCH
    if returncode != 0:
        return TransportErrorKind.NETWORK
    return TransportErrorKind.UNKNOWN


class RcloneTransport:
    """rclone transport driver. Backend Spec §7.1.3."""

    def __init__(self, *, binary: str = "rclone") -> None:
        self._binary = binary

    async def push(
        self,
        local: Path,
        remote: str,
        *,
        bwlimit_kibps: int | None = None,
    ) -> TransportResult:
        """Run ``rclone copy --checksum`` from ``local`` to ``remote``.

        ``remote`` is the full ``<remote_name>:<path>`` string per the
        rclone spec. ``bwlimit_kibps`` (KiB/s) is forwarded as
        ``--bwlimit <K>K`` when set.

        Returns a :class:`TransportResult` describing the outcome. A
        process-spawn failure (binary missing) raises
        :class:`TransportError` because no retry will help -- the lab
        admin needs to install the binary.
        """
        cmd: list[str] = [self._binary, "copy", "--checksum"]
        if bwlimit_kibps is not None and bwlimit_kibps > 0:
            cmd.extend(["--bwlimit", f"{bwlimit_kibps}K"])
        cmd.extend([str(local), remote])
        _log.debug("rclone cmd: %s", shlex.join(cmd))

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            msg = f"rclone binary not found: {self._binary!r}"
            raise TransportError(msg) from exc

        stdout_b, stderr_b = await proc.communicate()
        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        rc = proc.returncode if proc.returncode is not None else -1

        if rc == 0:
            return TransportResult(ok=True, returncode=0, stdout=stdout, stderr=stderr)

        kind = _classify_failure(stderr, rc)
        _log.warning("rclone failed rc=%d kind=%s", rc, kind.value)
        return TransportResult(
            ok=False,
            error_kind=kind,
            stderr=stderr,
            stdout=stdout,
            returncode=rc,
        )
