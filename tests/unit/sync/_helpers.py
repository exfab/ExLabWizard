"""Shared helpers for ``tests/unit/sync/`` test modules.

Kept under a leading-underscore name so pytest does not treat it as a
test module. Public entry points:

* :func:`local_check_factory` -- a ``check_callable_factory`` whose
  closure returns a perfect :class:`CheckResult` synthesised from the
  ``--files-from`` payload. The injected stub push callables in these
  unit tests are no-ops (they don't actually transfer files), so the
  verifier can't probe a real remote subtree; this fixture mimics a
  perfect remote by claiming every file in the requested subset is
  equal. The cleanup hash-gate consumes this.
* :func:`corrupt_one_check_factory` -- variant whose closure flags one
  named file as ``differ`` so the cleanup hash-gate mismatch path can be
  exercised.
* :func:`local_lsjson_factory` -- an ``lsjson_callable_factory`` whose
  closure walks the local run subtree and returns a
  :class:`RemoteManifest` whose entries match every local file's size
  AND modtime. Mirrors a perfect remote for the routine post-push
  reconcile and the cleanup existence probe.
* :func:`missing_one_lsjson_factory` -- variant whose closure omits one
  named file from the manifest so the reconcile-incomplete / cleanup
  existence-probe-fail paths can be exercised.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

from exlab_wizard.config.models import EquipmentConfig
from exlab_wizard.constants import CACHE_DIR_NAME
from exlab_wizard.sync.manifest import RemoteEntry, RemoteManifest
from exlab_wizard.sync.transports.rclone import CheckResult

__all__ = [
    "corrupt_one_check_factory",
    "local_check_factory",
    "local_lsjson_factory",
    "missing_one_lsjson_factory",
]


def _read_files_from(files_from: Path | None) -> tuple[str, ...]:
    """Read run-relative paths from a ``--files-from`` tempfile."""
    if files_from is None:
        return ()
    try:
        text = files_from.read_text(encoding="utf-8")
    except OSError:
        return ()
    return tuple(line.strip() for line in text.splitlines() if line.strip())


def _walk_local(run: Path) -> dict[str, RemoteEntry]:
    """Build a perfect remote manifest from the local run subtree.

    Each non-cache regular file becomes a :class:`RemoteEntry` whose size
    and ``mod_time`` exactly match the local file, so
    :meth:`RemoteManifest.matches` credits it within any non-negative
    tolerance.
    """
    entries: dict[str, RemoteEntry] = {}
    if not run.exists() or not run.is_dir():
        return entries
    for path in sorted(run.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(run)
        if CACHE_DIR_NAME in rel.parts:
            continue
        stat = path.stat()
        mod_time = (
            datetime.fromtimestamp(stat.st_mtime, tz=UTC)
            .isoformat()
            .replace("+00:00", "Z")
        )
        entries[rel.as_posix()] = RemoteEntry(
            size=stat.st_size, mod_time=mod_time, is_dir=False
        )
    return entries


def local_lsjson_factory() -> Callable[[EquipmentConfig], Callable[..., Awaitable[RemoteManifest]]]:
    """lsjson factory whose closure mirrors the local subtree perfectly.

    Returns a closure compatible with ``NASSyncClient.lsjson_callable_factory``:
    given ``(run)`` it walks the local run directory and returns a
    :class:`RemoteManifest` whose entries match every file's size and
    modtime. Equivalent to a perfect remote listing.
    """

    async def _lsjson(run: Path) -> RemoteManifest:
        return RemoteManifest(entries=_walk_local(run))

    def factory(_eq: EquipmentConfig) -> Callable[..., Awaitable[RemoteManifest]]:
        return _lsjson

    return factory


def missing_one_lsjson_factory(
    missing_rel: str,
) -> Callable[[EquipmentConfig], Callable[..., Awaitable[RemoteManifest]]]:
    """lsjson factory whose closure omits ``missing_rel`` from the manifest.

    Every other local file is reported with a matching size + modtime, so
    the routine reconcile credits the present files and re-queues for the
    missing one; the cleanup existence probe likewise fails on the gap.
    """

    async def _lsjson(run: Path) -> RemoteManifest:
        entries = _walk_local(run)
        entries.pop(missing_rel, None)
        return RemoteManifest(entries=entries)

    def factory(_eq: EquipmentConfig) -> Callable[..., Awaitable[RemoteManifest]]:
        return _lsjson

    return factory


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
