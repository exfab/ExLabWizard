"""rclone transport driver. Backend Spec §7.1.3.

Single transport binary for the NAS sync subsystem. Push uses
``rclone copy --checksum --files-from``; verify uses ``rclone check
--download --combined`` which streams remote bytes back and computes
SHA-256 locally (the only way to integrity-check SFTP and SMB backends,
which expose no server-side hashing).

The driver is intentionally thin: it builds an argv, hands it to
:func:`exlab_wizard.sync.transports._run.run_subprocess`, and
translates the exit-code + stderr-substring into one of the
``TransportErrorKind`` retry classes.

rclone.conf NAS-sync migration: the connection (host, credentials,
backend type) is defined entirely by a named remote in the operator's
``rclone.conf``. The driver injects no credentials and never sees a
password -- it only passes ``--config <path>`` when the ``nas:`` block
pins one.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from pathlib import Path

from exlab_wizard.logging import get_logger
from exlab_wizard.sync.transports import (
    TransportError,
    TransportErrorKind,
    TransportResult,
)
from exlab_wizard.sync.transports._run import run_subprocess

__all__ = [
    "AboutResult",
    "CheckResult",
    "RcloneDriver",
]

_log = get_logger(__name__)


# Substrings that indicate authentication failure rather than a transient
# network error. Match case-insensitive against the stderr.
_AUTH_FAILURE_MARKERS: tuple[str, ...] = (
    "auth_error",
    "authentication failed",
    "permission denied",
    "401 unauthorized",
    "403 forbidden",
    "access denied",
    "nt_status_logon_failure",
    "nt_status_access_denied",
)


def _classify_failure(stderr: str, returncode: int) -> TransportErrorKind:
    """Map a (returncode, stderr) into a :class:`TransportErrorKind`.

    Auth failures (auth markers, ``401``, ``403``, "permission denied")
    are terminal; "hash mismatch" / "checksum mismatch" surfaces as
    :attr:`TransportErrorKind.HASH_MISMATCH`; every other non-zero code
    is treated as a retryable network error.
    """
    lowered = stderr.lower()
    if any(marker in lowered for marker in _AUTH_FAILURE_MARKERS):
        return TransportErrorKind.AUTH
    if "hash mismatch" in lowered or "checksum mismatch" in lowered:
        return TransportErrorKind.HASH_MISMATCH
    if returncode != 0:
        return TransportErrorKind.NETWORK
    return TransportErrorKind.UNKNOWN


# ---------------------------------------------------------------------------
# Public DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CheckResult:
    """Parsed result of a ``rclone check --combined`` run.

    The combined-output format emits one prefixed line per file:

    - ``= path`` -- present and identical on both sides
    - ``* path`` -- present on both sides but differs
    - ``+ path`` -- present on the destination only
    - ``- path`` -- missing on the destination
    - ``! path`` -- error encountered checking this path

    Each field below carries the run-relative POSIX paths corresponding
    to its prefix.
    """

    equal: tuple[str, ...] = ()
    differ: tuple[str, ...] = ()
    extra_on_dst: tuple[str, ...] = ()
    missing_on_dst: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AboutResult:
    """Outcome of ``rclone about <remote> --json``.

    Surfaces as the equipment-probe response. ``ok`` flips true when
    rclone returned 0; ``reason`` carries the classified failure mode
    otherwise. ``info`` holds the parsed JSON payload on success
    (free-space, used, etc.) so the Settings panel can render it.
    """

    ok: bool
    reason: str | None = None
    info: dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


class RcloneDriver:
    """rclone transport driver. Backend Spec §7.1.3."""

    def __init__(
        self,
        *,
        binary: str = "rclone",
        config_path: str | None = None,
        transfers: int | None = None,
        checkers: int | None = None,
    ) -> None:
        self._binary = binary
        self._config_path = config_path or None
        self._transfers = transfers
        self._checkers = checkers

    def _global_flags(self) -> list[str]:
        """Flags valid on every rclone subcommand we invoke."""
        flags: list[str] = []
        if self._config_path:
            flags.extend(["--config", self._config_path])
        return flags

    def _checkers_flags(self) -> list[str]:
        """``--checkers <n>`` when the parallelism dial is set, else empty.

        Shared by ``push`` / ``check`` / ``lsjson`` -- every subcommand that
        runs a checker pass -- so the dial is forwarded the same way in one
        place.
        """
        if self._checkers is None:
            return []
        return ["--checkers", str(self._checkers)]

    async def push(
        self,
        local: Path,
        remote: str,
        *,
        bwlimit_kibps: int | None = None,
        files_from: Path | None = None,
    ) -> TransportResult:
        """Run ``rclone copy --checksum`` from ``local`` to ``remote``.

        ``remote`` is the full ``<remote_name>:<path>`` string; the named
        remote (and its credentials) lives in the operator's
        ``rclone.conf``. ``bwlimit_kibps`` is forwarded as
        ``--bwlimit <K>K`` when set; ``files_from`` is forwarded as
        ``--files-from <path>`` so only a subset of the local tree
        transfers.

        Returns a :class:`TransportResult`. A spawn failure raises
        :class:`TransportError` so the queue terminates rather than
        looping on a missing binary.
        """
        cmd: list[str] = [self._binary, "copy", "--checksum"]
        cmd.extend(self._global_flags())
        if self._transfers is not None:
            cmd.extend(["--transfers", str(self._transfers)])
        cmd.extend(self._checkers_flags())
        if bwlimit_kibps is not None and bwlimit_kibps > 0:
            cmd.extend(["--bwlimit", f"{bwlimit_kibps}K"])
        if files_from is not None:
            cmd.extend(["--files-from", str(files_from)])
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

    async def check(
        self,
        local: Path,
        remote: str,
        *,
        files_from: Path,
    ) -> CheckResult:
        """Run ``rclone check --download --files-from --combined`` over ``files_from``.

        Streams the remote files back to compute their SHA-256 locally
        (the only way to integrity-check SFTP and SMB backends, which
        expose no server-side hashing). ``--combined`` writes one
        ``= / * / + / - / !`` line per file to a tempfile that this
        method parses and returns as a :class:`CheckResult`.

        Raises :class:`TransportError` with a classified ``error_kind``
        when rclone itself failed (auth / network / unknown) -- a clean
        run with files in the ``differ`` or ``missing_on_dst`` columns
        returns ``ok=True`` so the caller can route partial-failure
        reconciliation correctly.
        """
        import tempfile

        combined_handle = tempfile.NamedTemporaryFile(  # noqa: SIM115 -- explicit close
            mode="w",
            encoding="utf-8",
            prefix="exlab-rclone-combined-",
            suffix=".txt",
            delete=False,
        )
        combined_path = Path(combined_handle.name)
        combined_handle.close()

        cmd: list[str] = [
            self._binary,
            "check",
            "--download",
            "--files-from",
            str(files_from),
            "--combined",
            str(combined_path),
            str(local),
            remote,
        ]
        cmd[1:1] = self._global_flags()
        cmd.extend(self._checkers_flags())
        _log.debug("rclone check cmd: %s", shlex.join(cmd))

        try:
            try:
                rc, _stdout, stderr = await run_subprocess(cmd)
            except FileNotFoundError as exc:
                msg = f"rclone binary not found: {self._binary!r}"
                raise TransportError(msg) from exc

            # rclone check returns non-zero whenever any file differs.
            # Differences are an expected and parseable outcome, so the
            # combined-output file must still be read on rc != 0; only an
            # auth / network failure with no combined output should raise.
            # One-shot read of a small tempfile written by rclone — sync I/O
            # is fine here and avoids a trio/anyio dep in the sync subsystem.
            try:
                combined_text = combined_path.read_text(encoding="utf-8")  # noqa: ASYNC240
            except OSError:
                combined_text = ""

            if rc != 0 and not combined_text:
                kind = _classify_failure(stderr, rc)
                if kind in (
                    TransportErrorKind.AUTH,
                    TransportErrorKind.NETWORK,
                    TransportErrorKind.UNKNOWN,
                ):
                    _log.warning("rclone check failed rc=%d kind=%s", rc, kind.value)
                    msg = f"rclone check failed rc={rc} kind={kind.value}: {stderr.strip()}"
                    raise TransportError(msg, error_kind=kind)

            return _parse_combined(combined_text)
        finally:
            combined_path.unlink(missing_ok=True)  # noqa: ASYNC240

    async def about(
        self,
        remote: str,
    ) -> AboutResult:
        """Run ``rclone about <remote> --json`` -- the equipment probe.

        Used by the Settings "Test connection" affordance. Confirms
        authentication and reachability; surfaces parsed free-space
        info on success. Failure paths are translated into
        :class:`AboutResult` rather than raised, so the UI panel can
        render the reason inline.
        """
        import json as _json

        cmd: list[str] = [self._binary, "about", remote, "--json"]
        cmd[1:1] = self._global_flags()
        _log.debug("rclone about cmd: %s", shlex.join(cmd))

        try:
            rc, stdout, stderr = await run_subprocess(cmd)
        except FileNotFoundError:
            return AboutResult(ok=False, reason="rclone binary not found")

        if rc != 0:
            kind = _classify_failure(stderr, rc)
            return AboutResult(ok=False, reason=f"{kind.value}: {stderr.strip()}")

        try:
            parsed = _json.loads(stdout) if stdout.strip() else {}
        except _json.JSONDecodeError:
            return AboutResult(ok=True, info={})
        info = {key: int(value) for key, value in parsed.items() if isinstance(value, int | float)}
        return AboutResult(ok=True, info=info)

    async def lsjson(self, remote: str, *, recursive: bool = True) -> str:
        """Run ``rclone lsjson`` (read-only) and return the raw JSON array text.

        Listing only — no transfer, no remote mutation. ``recursive`` adds
        ``-R`` so a whole run subtree returns in one call. Raises
        :class:`TransportError` with a classified ``error_kind`` on a
        non-zero exit so callers route auth/network failures the same way
        as push/check.
        """
        cmd: list[str] = [self._binary, "lsjson", *self._global_flags()]
        if recursive:
            cmd.append("-R")
        cmd.extend(self._checkers_flags())
        cmd.append(remote)
        _log.debug("rclone lsjson cmd: %s", shlex.join(cmd))
        try:
            rc, stdout, stderr = await run_subprocess(cmd)
        except FileNotFoundError as exc:
            msg = f"rclone binary not found: {self._binary!r}"
            raise TransportError(msg) from exc
        if rc != 0:
            kind = _classify_failure(stderr, rc)
            msg = f"rclone lsjson failed rc={rc} kind={kind.value}: {stderr.strip()}"
            raise TransportError(msg, error_kind=kind)
        return stdout

    async def listremotes(self) -> tuple[str, ...]:
        """Return the remote names defined in rclone.conf (each incl. trailing ``:``)

        Offline and cheap — no network. Used by the setup-availability gate and
        the Settings remote badge. A missing/unreadable config yields ``()`` so
        callers treat "no remotes" the same as "remote not found".
        """
        cmd = [self._binary, "listremotes", *self._global_flags()]
        try:
            rc, stdout, _stderr = await run_subprocess(cmd)
        except FileNotFoundError:
            return ()
        if rc != 0:
            return ()
        return tuple(line.strip() for line in stdout.splitlines() if line.strip())


# ---------------------------------------------------------------------------
# Combined-output parser
# ---------------------------------------------------------------------------


def _parse_combined(text: str) -> CheckResult:
    """Parse ``rclone check --combined`` output into a :class:`CheckResult`.

    Each non-empty line begins with one of ``=``, ``*``, ``+``, ``-``,
    ``!`` followed by a space and the file path. Unknown prefixes are
    silently ignored so a future rclone format extension doesn't crash
    the verifier.
    """
    equal: list[str] = []
    differ: list[str] = []
    extra_on_dst: list[str] = []
    missing_on_dst: list[str] = []
    errors: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line or len(line) < 2 or line[1] != " ":
            continue
        prefix = line[0]
        path = line[2:]
        match prefix:
            case "=":
                equal.append(path)
            case "*":
                differ.append(path)
            case "+":
                extra_on_dst.append(path)
            case "-":
                missing_on_dst.append(path)
            case "!":
                errors.append(path)
            case _:
                _log.debug("rclone check: unknown prefix %r in line %r", prefix, line)
    return CheckResult(
        equal=tuple(equal),
        differ=tuple(differ),
        extra_on_dst=tuple(extra_on_dst),
        missing_on_dst=tuple(missing_on_dst),
        errors=tuple(errors),
    )
