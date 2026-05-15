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

import shlex
from pathlib import Path

from exlab_wizard.logging import get_logger
from exlab_wizard.sync.transports import (
    TransportError,
    TransportErrorKind,
    TransportResult,
)
from exlab_wizard.sync.transports._run import run_subprocess

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
            rc, stdout, stderr = await run_subprocess(cmd)
        except FileNotFoundError as exc:
            msg = f"rclone binary not found: {self._binary!r}"
            raise TransportError(msg) from exc

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

    async def hashsum(self, remote: str) -> dict[str, str]:
        """Probe ``remote`` via ``rclone hashsum sha256`` and parse the manifest.

        Returns a ``{relative-path: sha256-hex}`` dict mirroring the
        on-disk manifest format on success (``rc == 0``). The dict may
        legitimately be empty if the remote subtree contains no files.

        Failure modes are surfaced as :class:`TransportError` with the
        classified ``error_kind`` so the caller (the verifier / queue
        worker) can route via the spec-correct §7.1.5 retry path:

        - ``AUTH`` -- terminal FAILED.
        - ``NETWORK`` / ``UNKNOWN`` -- backoff retry.

        Spawn failure (binary missing) also raises :class:`TransportError`
        but with ``error_kind=None`` so the worker treats it as a
        non-terminal failure (operator can install the binary and the job
        will retry rather than terminating).
        """
        from exlab_wizard.sync.verifier import parse_manifest

        cmd: list[str] = [self._binary, "hashsum", "sha256", remote]
        _log.debug("rclone hashsum cmd: %s", shlex.join(cmd))

        try:
            rc, stdout, stderr = await run_subprocess(cmd)
        except FileNotFoundError as exc:
            msg = f"rclone binary not found: {self._binary!r}"
            raise TransportError(msg) from exc

        if rc != 0:
            kind = _classify_failure(stderr, rc)
            _log.warning("rclone hashsum failed rc=%d kind=%s", rc, kind.value)
            msg = f"rclone hashsum failed rc={rc} kind={kind.value}: {stderr.strip()}"
            raise TransportError(msg, error_kind=kind)

        return parse_manifest(stdout)
