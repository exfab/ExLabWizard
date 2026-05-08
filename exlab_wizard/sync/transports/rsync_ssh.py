"""rsync-over-SSH transport driver. Backend Spec §7.1.3.

Shells out to ``rsync -avz --checksum --partial -e "ssh -i <key> -o
BatchMode=yes" --bwlimit=<K> <local> <user>@<host>:<path>``. SSH
authentication is **key-based only**; the spec rejects password auth at
config-validation time (see :class:`exlab_wizard.config.models.RsyncSshTransport`).

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

__all__ = ["RsyncSshTransport"]

_log = get_logger(__name__)


# rsync exit codes (selection used by the classifier):
# 0  = success
# 5  = error starting client-server protocol (often auth failure)
# 23 = some files could not be transferred (often permission-denied)
# 12 = error in rsync protocol data stream
# 30 = timeout in data send/receive
_RSYNC_AUTH_RETURNCODES: frozenset[int] = frozenset({5, 23})


# Substrings that indicate authentication failure rather than a transient
# network error. Match case-insensitive.
_AUTH_FAILURE_MARKERS: tuple[str, ...] = (
    "auth_error",
    "permission denied (publickey)",
    "permission denied",
    "authentication failed",
    "host key verification failed",
)


def _classify_failure(stderr: str, returncode: int) -> TransportErrorKind:
    """Map a (returncode, stderr) into a :class:`TransportErrorKind`."""
    lowered = stderr.lower()
    if any(marker in lowered for marker in _AUTH_FAILURE_MARKERS):
        return TransportErrorKind.AUTH
    if returncode in _RSYNC_AUTH_RETURNCODES and "permission" in lowered:
        return TransportErrorKind.AUTH
    if "hash mismatch" in lowered or "checksum mismatch" in lowered:
        return TransportErrorKind.HASH_MISMATCH
    if returncode != 0:
        return TransportErrorKind.NETWORK
    return TransportErrorKind.UNKNOWN


def _ssh_command(key: Path) -> list[str]:
    """Return the canonical ``ssh`` argv prefix used by both push and hashsum.

    Centralises the ``-i <key> -o BatchMode=yes`` invocation so the push
    path (which embeds it as a single ``-e`` value via ``shlex.join``) and
    the hashsum path (which uses the list directly as the argv prefix)
    cannot drift.
    """
    return ["ssh", "-i", str(key), "-o", "BatchMode=yes"]


class RsyncSshTransport:
    """rsync-over-SSH transport driver. Backend Spec §7.1.3."""

    def __init__(self, *, binary: str = "rsync") -> None:
        self._binary = binary

    async def push(
        self,
        local: Path,
        ssh_target: str,
        ssh_key_path: Path,
        remote_path: str,
        *,
        bwlimit_kibps: int | None = None,
    ) -> TransportResult:
        """Run ``rsync -avz --checksum`` from ``local`` to ``ssh_target:remote_path``.

        ``ssh_target`` is ``<user>@<host>``. ``ssh_key_path`` is forwarded
        via ``-e 'ssh -i <key> -o BatchMode=yes'`` so the driver never
        prompts for a password.

        Returns a :class:`TransportResult`. Raises :class:`TransportError`
        when the rsync binary is missing (no retry will help).
        """
        ssh_cmd = shlex.join(_ssh_command(ssh_key_path))
        cmd: list[str] = [
            self._binary,
            "-avz",
            "--checksum",
            "--partial",
            "-e",
            ssh_cmd,
        ]
        if bwlimit_kibps is not None and bwlimit_kibps > 0:
            cmd.append(f"--bwlimit={bwlimit_kibps}")
        cmd.append(str(local))
        cmd.append(f"{ssh_target}:{remote_path}")
        _log.debug("rsync cmd: %s", shlex.join(cmd))

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            msg = f"rsync binary not found: {self._binary!r}"
            raise TransportError(msg) from exc

        stdout_b, stderr_b = await proc.communicate()
        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        rc = proc.returncode if proc.returncode is not None else -1

        if rc == 0:
            return TransportResult(ok=True, returncode=0, stdout=stdout, stderr=stderr)

        kind = _classify_failure(stderr, rc)
        _log.warning("rsync failed rc=%d kind=%s", rc, kind.value)
        return TransportResult(
            ok=False,
            error_kind=kind,
            stderr=stderr,
            stdout=stdout,
            returncode=rc,
        )

    async def hashsum(
        self,
        ssh_target: str,
        ssh_key_path: Path,
        remote_path: str,
    ) -> dict[str, str]:
        """Probe ``remote_path`` via ``ssh ... find ... sha256sum`` and parse.

        Returns a ``{relative-path: sha256-hex}`` dict whose keys are
        stripped of the ``remote_path`` prefix so they match the local
        manifest's relative-path convention (see
        :meth:`exlab_wizard.sync.verifier.Verifier.compute_local_manifest`)
        on success (``rc == 0``). The dict may legitimately be empty if
        the remote subtree contains no files.

        Failure modes are surfaced as :class:`TransportError` with the
        classified ``error_kind`` so the caller (the verifier / queue
        worker) can route via the spec-correct §7.1.5 retry path:

        - ``AUTH`` -- terminal FAILED.
        - ``NETWORK`` / ``UNKNOWN`` -- backoff retry.

        Spawn failure (binary missing) also raises :class:`TransportError`
        but with ``error_kind=None`` so the worker treats it as a
        non-terminal failure (operator can install ssh and the job will
        retry rather than terminating).
        """
        from exlab_wizard.sync.verifier import parse_manifest

        remote_cmd = f"find {shlex.quote(remote_path)} -type f -exec sha256sum {{}} +"
        cmd: list[str] = [*_ssh_command(ssh_key_path), ssh_target, remote_cmd]
        _log.debug("rsync ssh hashsum cmd: %s", shlex.join(cmd))

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            msg = "ssh binary not found: ssh"
            raise TransportError(msg) from exc

        stdout_b, stderr_b = await proc.communicate()
        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        rc = proc.returncode if proc.returncode is not None else -1

        if rc != 0:
            kind = _classify_failure(stderr, rc)
            _log.warning("rsync ssh hashsum failed rc=%d kind=%s", rc, kind.value)
            msg = f"rsync ssh hashsum failed rc={rc} kind={kind.value}: {stderr.strip()}"
            raise TransportError(msg, error_kind=kind)

        # Parse, then strip the remote_path prefix so keys are run-relative.
        absolute_manifest = parse_manifest(stdout)
        prefix = remote_path.rstrip("/") + "/"
        relative: dict[str, str] = {}
        for path_key, hex_digest in absolute_manifest.items():
            if path_key.startswith(prefix):
                relative[path_key[len(prefix) :]] = hex_digest
            elif path_key == remote_path.rstrip("/"):
                # A single-file remote target. Use the basename.
                relative[Path(path_key).name] = hex_digest
            else:
                # Fallback: keep the original key.
                relative[path_key] = hex_digest
        return relative
