"""SHA-256 verifier wrapper around ``rclone check --download``.

Rclone-only NAS sync migration (2026-05-26). The verifier is now a thin
adapter over :meth:`exlab_wizard.sync.transports.rclone.RcloneDriver.check`
that translates a :class:`CheckResult` into the :class:`VerifyResult`
shape the queue worker already consumes. The actual integrity guarantee
comes from ``rclone check --download --combined`` -- rclone streams the
remote files back to the wizard, hashes them locally, and writes a
``=/*/+/-/!`` line per file to a tempfile that the driver parses.

The Slot A SHA-256 capture (the durable
``sync_state.json:files[*].verified_sha256`` field) is owned by
:func:`exlab_wizard.sync.nas_client._compute_local_shas` -- the verifier
itself never sees a local SHA. This keeps the verifier a leaf abstraction
that depends only on the rclone driver, and keeps the SHA-capture logic
local to the one code path that has access to the freshly-read local
bytes.

The previous Python SHA pipeline and the durable
``<run>/.exlab-wizard/checksums.sha256`` artefact are gone (the spec
chose `rclone check` as the authority on integrity at sync time).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from exlab_wizard.logging import get_logger
from exlab_wizard.sync.transports import TransportError, TransportErrorKind

if TYPE_CHECKING:
    from exlab_wizard.sync.transports.rclone import RcloneDriver

__all__ = ["Verifier", "VerifyResult"]

_log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class VerifyResult:
    """Outcome of one ``rclone check --download`` pass against a remote.

    ``ok`` is True iff no files differ, none are missing on the
    destination, and the rclone subprocess reported no per-file errors.
    Files in ``mismatched`` / ``missing`` / ``errors`` are run-relative
    POSIX paths drawn from the ``--combined`` output. ``extra`` lists
    files present on the destination but not in the source -- it does
    not flip ``ok`` (it is informational, mirroring the pre-migration
    contract).

    ``error_kind`` is set when the rclone subprocess itself failed
    (auth / network / unknown) before producing usable combined
    output. The queue worker keys off this field to route through the
    spec §7.1.5 retry policy.
    """

    ok: bool
    mismatched: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    extra: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    error_kind: TransportErrorKind | None = None
    # Verified rel-paths (the rclone ``=`` lines). The worker uses this
    # set when crediting files into ``sync_state.json`` so the reconcile
    # path does not have to re-derive it from ``job.files`` minus the
    # bad subsets.
    verified: tuple[str, ...] = ()


class Verifier:
    """Verifier: ``rclone check`` wrapper, no Python SHA pipeline.

    Constructed with an :class:`RcloneDriver`; production callers pass
    the same driver instance the push path uses so subprocess settings
    (binary path, etc.) stay consistent. Tests can pass a stub driver
    whose ``check`` returns a canned :class:`CheckResult`.
    """

    def __init__(self, driver: RcloneDriver | None = None) -> None:
        if driver is None:
            from exlab_wizard.sync.transports.rclone import RcloneDriver as _Driver

            driver = _Driver()
        self._driver = driver

    async def verify(
        self,
        run_path: Path,
        remote: str,
        *,
        files_from: Path,
        env: dict[str, str] | None = None,
        mask_for_log: tuple[str, ...] = (),
    ) -> VerifyResult:
        """Run ``rclone check --download`` over ``files_from`` and translate.

        Raises :class:`TransportError` from the driver only on a spawn
        failure (the rclone binary is missing); every other failure mode
        -- auth / network / hash-mismatch -- is folded into the returned
        :class:`VerifyResult`.
        """
        try:
            check_result = await self._driver.check(
                run_path,
                remote,
                files_from=files_from,
                env=env,
                mask_for_log=mask_for_log,
            )
        except TransportError as exc:
            _log.warning("rclone check transport error: %s", exc)
            return VerifyResult(
                ok=False,
                error_kind=exc.error_kind,
            )

        ok = not check_result.differ and not check_result.missing_on_dst and not check_result.errors
        return VerifyResult(
            ok=ok,
            mismatched=check_result.differ,
            missing=check_result.missing_on_dst,
            extra=check_result.extra_on_dst,
            errors=check_result.errors,
            verified=check_result.equal,
        )
