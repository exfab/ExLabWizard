"""Shared helpers for ``tests/unit/sync/`` test modules.

Kept under a leading-underscore name so pytest does not treat it as a
test module. Public entry points:

* :func:`local_check_factory` -- a ``check_callable_factory`` whose
  closure returns a perfect :class:`CheckResult` synthesised from the
  ``--files-from`` payload. The injected stub push callables in these
  unit tests are no-ops (they don't actually transfer files), so the
  verifier can't probe a real remote subtree; this fixture mimics a
  perfect remote by claiming every file in the requested subset is
  equal.
* :func:`corrupt_one_check_factory` -- variant whose closure flags one
  named file as ``differ`` so the per-file reconciliation path can be
  exercised.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from exlab_wizard.config.models import EquipmentConfig
from exlab_wizard.sync.transports.rclone import CheckResult

__all__ = ["corrupt_one_check_factory", "local_check_factory"]


def _read_files_from(files_from: Path | None) -> tuple[str, ...]:
    """Read run-relative paths from a ``--files-from`` tempfile."""
    if files_from is None:
        return ()
    try:
        text = files_from.read_text(encoding="utf-8")
    except OSError:
        return ()
    return tuple(line.strip() for line in text.splitlines() if line.strip())


def local_check_factory() -> Callable[[EquipmentConfig], Callable[..., Awaitable[CheckResult]]]:
    """Check factory whose closure declares every file equal.

    Returns a closure compatible with ``NASSyncClient.check_callable_factory``:
    given ``(local, *, files_from)`` it reads the files-from list and
    returns a :class:`CheckResult` with every entry in ``equal``.
    Equivalent to a no-error verify pass against a perfect remote.
    """

    async def _check(local: Path, *, files_from: Path) -> CheckResult:
        del local
        return CheckResult(equal=_read_files_from(files_from))

    def factory(_eq: EquipmentConfig) -> Callable[..., Awaitable[CheckResult]]:
        return _check

    return factory


def corrupt_one_check_factory(
    corrupt_rel: str,
) -> Callable[[EquipmentConfig], Callable[..., Awaitable[CheckResult]]]:
    """Check factory whose closure flags ``corrupt_rel`` as ``differ``.

    Every other file in the files-from list is reported as ``equal``,
    so the partial-failure reconcile path -- the good files get
    credited in ``sync_state.json`` while the batch job routes to
    HASH_MISMATCH retry / terminal -- is exercised.
    """

    async def _check(local: Path, *, files_from: Path) -> CheckResult:
        del local
        files = _read_files_from(files_from)
        equal = tuple(rel for rel in files if rel != corrupt_rel)
        differ = (corrupt_rel,) if corrupt_rel in files else ()
        return CheckResult(equal=equal, differ=differ)

    def factory(_eq: EquipmentConfig) -> Callable[..., Awaitable[CheckResult]]:
        return _check

    return factory
