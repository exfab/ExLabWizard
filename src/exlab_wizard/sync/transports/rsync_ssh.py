"""rsync-over-ssh transport driver.

rsync-over-ssh NAS transport design (2026-06-10). Mirrors
:class:`~exlab_wizard.sync.transports.rclone.RcloneDriver`'s
thin-wrapper philosophy: build an argv, hand it to
:func:`~exlab_wizard.sync.transports._run.run_subprocess`, classify the
outcome. Every op is built from rsync protocol primitives only — the
NAS allowlists exactly the ``rsync --server`` ssh channel (no other
remote command execution, no interactive login, no SFTP), so there is
no ``ssh host df`` / ``find`` / ``sha256sum`` anywhere in this module.

Targets are full ``user@host:/path`` strings composed by the caller
(the same ``<remote>:<path>`` shape the rclone driver receives).
Credentials are ssh keys only (``BatchMode=yes``); host keys are
pre-provisioned in ``known_hosts`` via ``ssh-keyscan`` during setup.
"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import TYPE_CHECKING

from exlab_wizard.logging import get_logger
from exlab_wizard.sync.manifest import parse_rsync_listing
from exlab_wizard.sync.transports import (
    TransportError,
    TransportErrorKind,
    TransportResult,
)
from exlab_wizard.sync.transports._run import run_subprocess
from exlab_wizard.sync.transports.rclone import AboutResult, CheckResult

if TYPE_CHECKING:
    from exlab_wizard.sync.manifest import RemoteManifest

__all__ = ["RsyncSshDriver"]

_log = get_logger(__name__)


# ssh-level failures that are configuration problems, not transient
# network errors. Matched case-insensitively against stderr. The
# permission-denied marker is deliberately anchored on ssh's
# parenthesized auth-method list ("Permission denied (publickey...") so
# a remote per-file filesystem error -- rsync's "Permission denied (13)"
# -- stays on the retryable rc-23 path instead of terminating the job.
_AUTH_FAILURE_MARKERS: tuple[str, ...] = (
    "permission denied (publickey",
    "host key verification failed",
    "too many authentication failures",
    "no supported authentication",
)

# rsync exit codes that are transient / retryable: 5 client-server
# protocol start, 10 socket I/O, 12 protocol data stream, 23/24 partial
# transfer (incl. vanished source files), 30 timeout, 35 daemon-connect
# timeout. 255 is the ssh transport itself failing.
_NETWORK_RETURNCODES: frozenset[int] = frozenset({5, 10, 12, 23, 24, 30, 35, 255})


def _classify_failure(stderr: str, returncode: int) -> TransportErrorKind:
    """Map a (stderr, returncode) into a :class:`TransportErrorKind`."""
    lowered = stderr.lower()
    if any(marker in lowered for marker in _AUTH_FAILURE_MARKERS):
        return TransportErrorKind.AUTH
    if returncode in _NETWORK_RETURNCODES:
        return TransportErrorKind.NETWORK
    return TransportErrorKind.UNKNOWN


def _synthesize_check(requested: tuple[str, ...], stdout: str) -> CheckResult:
    """Translate dry-run itemized output into a :class:`CheckResult`.

    Itemize lines are ``YXcstpoguax <path>`` — an update-type char, a
    file-type char, then attribute flags. The attribute tail is 9 chars
    in rsync 3.x and shorter in openrsync, so flags are read
    positionally from the front, never by total length. Only
    regular-file lines (``f`` at index 1) participate; ``.``-type lines
    (no transfer needed) and unmatched lines fall through to ``equal``.

    Accepted contract difference vs rclone's ``CheckResult``: ``errors``
    is always ``()`` here. rsync itemize has no per-file error line (no
    ``!``-prefix equivalent) — failures surface on stderr with a
    non-zero exit, which :meth:`RsyncSshDriver.check` raises as a
    classified :class:`TransportError` before this synthesis runs.
    """
    differ: list[str] = []
    missing: list[str] = []
    flagged: set[str] = set()
    for raw_line in stdout.splitlines():
        line = raw_line.rstrip()
        flags, sep, path = line.partition(" ")
        path = path.strip()
        if not sep or not path or len(flags) < 3:
            continue
        if flags[0] not in "<>ch." or flags[1] != "f":
            continue
        tail = flags[2:]
        if tail and all(ch == "+" for ch in tail):
            missing.append(path)
            flagged.add(path)
        elif flags[2] == "c":
            differ.append(path)
            flagged.add(path)
        # any other combination (e.g. ``.f..t......``) is an attr-only
        # change: content equal under --checksum -> credited via `equal`.
    equal = tuple(rel for rel in requested if rel not in flagged)
    return CheckResult(
        equal=equal, differ=tuple(differ), missing_on_dst=tuple(missing)
    )


class RsyncSshDriver:
    """rsync-over-ssh transport driver (implements ``NasTransportDriver``).

    ``ssh_extra_opts`` is a **test seam only** — each entry is passed to
    ssh as ``-o <entry>``.  Production config does not expose it and it
    must never be set in production code paths.  The integration-test
    fixture uses it to point ssh at a throwaway ``known_hosts`` file
    (``UserKnownHostsFile=<tmp>``) and accept the container's first-seen
    host key (``StrictHostKeyChecking=accept-new``) without mutating the
    developer's real ``~/.ssh/known_hosts``.
    """

    def __init__(
        self,
        *,
        binary: str = "rsync",
        ssh_port: int = 22,
        ssh_identity_file: str = "",
        ssh_extra_opts: tuple[str, ...] = (),
    ) -> None:
        self._binary = binary
        self._ssh_port = ssh_port
        self._ssh_identity_file = ssh_identity_file
        self._ssh_extra_opts = ssh_extra_opts

    def _ssh_command(self) -> str:
        """Render the ``-e`` remote-shell string.

        ``BatchMode=yes`` is mandatory: an unknown host key or a key
        passphrase prompt must fail fast (classified AUTH) rather than
        hang the sync worker. No ``StrictHostKeyChecking`` relaxation —
        host keys are pre-provisioned via ``ssh-keyscan``.

        ``_ssh_extra_opts`` entries are appended as ``-o <opt>`` pairs
        (test seam; production never sets this).
        """
        parts = ["ssh", "-p", str(self._ssh_port), "-o", "BatchMode=yes"]
        if self._ssh_identity_file:
            parts.extend(["-i", str(Path(self._ssh_identity_file).expanduser())])
        for opt in self._ssh_extra_opts:
            parts.extend(["-o", opt])
        return shlex.join(parts)

    async def push(
        self,
        local: Path,
        remote: str,
        *,
        bwlimit_kibps: int | None = None,
        files_from: Path | None = None,
    ) -> TransportResult:
        """Run ``rsync -rt`` from ``local`` to ``remote``.

        ``-t`` is mandatory — the manifest reconcile tolerance and the
        quiescence poller both depend on mtime preservation. The
        trailing slash on the source copies the *contents* of ``local``
        (matching ``rclone copy`` semantics). No ``--checksum`` on push:
        integrity is owned by the verify path, not the transfer.
        """
        cmd: list[str] = [self._binary, "-rt", "-e", self._ssh_command()]
        if bwlimit_kibps is not None and bwlimit_kibps > 0:
            cmd.append(f"--bwlimit={bwlimit_kibps}")
        if files_from is not None:
            cmd.append(f"--files-from={files_from}")
        # ``--`` ends option parsing: a remote/path that begins with ``-``
        # (malformed config) must never be read as an rsync flag.
        cmd.extend(["--", f"{local}/", remote])
        _log.debug("rsync cmd: %s", shlex.join(cmd))

        try:
            rc, stdout, stderr = await run_subprocess(cmd)
        except FileNotFoundError as exc:
            msg = f"rsync binary not found: {self._binary!r}"
            raise TransportError(msg) from exc

        if rc == 0:
            return TransportResult(ok=True, returncode=0, stdout=stdout, stderr=stderr)
        kind = _classify_failure(stderr, rc)
        _log.warning("rsync push failed rc=%d kind=%s", rc, kind.value)
        return TransportResult(
            ok=False, error_kind=kind, stderr=stderr, stdout=stdout, returncode=rc
        )

    async def check(
        self, local: Path, remote: str, *, files_from: Path
    ) -> CheckResult:
        """Content verify via ``rsync -rni --checksum`` dry-run (resolved OQ-1).

        The remote rsync hashes whole files inside the rsync protocol
        (the same allowlisted channel as push — no separate remote
        command). Itemized output is synthesized into a
        :class:`CheckResult`: checksum-differ flag → ``differ``, all-+
        creation → ``missing_on_dst``, and ``equal`` is reconstructed as
        the ``files_from`` list minus the itemized paths (matching files
        are simply omitted from dry-run output). ``extra_on_dst`` is
        always ``()`` — a push-direction dry-run cannot see remote-only
        files; harmless, it never flips ``VerifyResult.ok``.

        Raises :class:`TransportError` with a classified ``error_kind``
        on any non-zero exit (rsync dry-runs exit 0 even when files
        differ; non-zero means the run itself failed).
        """
        cmd: list[str] = [
            self._binary,
            "-rni",
            "--checksum",
            "-e",
            self._ssh_command(),
            f"--files-from={files_from}",
            "--",
            f"{local}/",
            remote,
        ]
        _log.debug("rsync check cmd: %s", shlex.join(cmd))
        try:
            rc, stdout, stderr = await run_subprocess(cmd)
        except FileNotFoundError as exc:
            msg = f"rsync binary not found: {self._binary!r}"
            raise TransportError(msg) from exc
        if rc != 0:
            kind = _classify_failure(stderr, rc)
            msg = f"rsync check failed rc={rc} kind={kind.value}: {stderr.strip()}"
            raise TransportError(msg, error_kind=kind)

        try:
            requested = tuple(
                line.strip()
                for line in files_from.read_text(encoding="utf-8").splitlines()  # noqa: ASYNC240
                if line.strip()
            )
        except OSError:
            requested = ()
        return _synthesize_check(requested, stdout)

    async def lsjson_manifest(
        self, remote: str, *, strip_prefix: str = ""
    ) -> RemoteManifest:
        """List the remote subtree via ``--list-only -r`` (resolved OQ-2).

        ``strip_prefix`` is accepted for protocol parity but ignored —
        rsync listings are already relative to the listed target (the
        rclone lsjson path is run-rooted and needs stripping; this one
        is not). Raises :class:`TransportError` with a classified
        ``error_kind`` on non-zero exit, mirroring
        :meth:`RcloneDriver.lsjson`.
        """
        del strip_prefix
        cmd: list[str] = [
            self._binary,
            "--list-only",
            "-r",
            "-e",
            self._ssh_command(),
            "--",
            f"{remote}/",
        ]
        _log.debug("rsync list cmd: %s", shlex.join(cmd))
        try:
            rc, stdout, stderr = await run_subprocess(cmd)
        except FileNotFoundError as exc:
            msg = f"rsync binary not found: {self._binary!r}"
            raise TransportError(msg) from exc
        if rc != 0:
            kind = _classify_failure(stderr, rc)
            msg = f"rsync list failed rc={rc} kind={kind.value}: {stderr.strip()}"
            raise TransportError(msg, error_kind=kind)
        return parse_rsync_listing(stdout)

    async def about(self, remote: str) -> AboutResult:
        """Reachability + auth probe: non-recursive ``--list-only`` of ``remote``.

        Free-space info is unobtainable without remote command exec
        (which IT blocks), so ``info`` is always empty — the Settings
        panel already tolerates that (resolved OQ-5). rc 23 with no
        listing output means the path itself is absent: surfaced as a
        configuration reason, not a retryable network error
        (spec-review carve-out).
        """
        cmd: list[str] = [
            self._binary,
            "--list-only",
            "-e",
            self._ssh_command(),
            "--",
            f"{remote}/",
        ]
        _log.debug("rsync about cmd: %s", shlex.join(cmd))
        try:
            rc, stdout, stderr = await run_subprocess(cmd)
        except FileNotFoundError:
            return AboutResult(ok=False, reason="rsync binary not found")
        if rc == 0:
            return AboutResult(ok=True, info={})
        if rc == 23 and not stdout.strip():
            return AboutResult(
                ok=False, reason="base_root not found or inaccessible on the NAS"
            )
        kind = _classify_failure(stderr, rc)
        return AboutResult(ok=False, reason=f"{kind.value}: {stderr.strip()}")
