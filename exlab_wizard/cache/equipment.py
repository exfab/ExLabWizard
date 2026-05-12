"""Writer for ``equipment.json`` (registry) and ``test_runs.json`` (marker).

Backend Spec §11.4.1 (`equipment.json`), §11.4.2 (`test_runs.json`),
§4.4.5 (CacheWriter contract), §11.9 (schema-version policy).

Both files in this module are written rarely:

- ``equipment.json`` -- written on the first project creation under an
  equipment, plus on Settings-driven re-syncs whenever ``config.yaml`` is
  edited. Treated as a *registry* record (the equipment is a configured
  workstation peripheral, not an instance of a creation flow).
- ``test_runs.json`` -- a marker file written ONCE on the first test run
  within ``<equipment>/<project>/TestRuns/``. Subsequent test-run
  creations under the same project do NOT rewrite this file
  (§11.4.2).

Both writes use the canonical CacheWriter recipe (§4.4.5):

1. ``msgspec.json.encode`` for the typed encode (replaces stdlib
   ``json``; schema validation lives in the ``msgspec.Struct`` types).
2. Atomic write via tempfile + ``fsync`` + ``os.replace``.
3. Per-file advisory lock via ``filelock.FileLock`` so concurrent writers
   serialize without corrupting the file.
4. Async wrappers around the synchronous lock+write logic via
   ``asyncio.to_thread`` so the asyncio event loop is not blocked.

The reader path (:meth:`EquipmentCacheWriter.read_equipment`) does NOT
acquire the lock: per §4.4.5 a snapshot read is uncontended, and shared
locks in ``filelock`` would force the writer to wait on stale readers.
A reader that races with a writer will see either the pre-write content
(via the original inode) or the post-write content (via the
``os.replace`` atomic rename); both are valid points-in-time.

The :func:`require_schema_major` helper at module bottom is the shared
schema-major gate used by every cache writer in the package -- it
implements §11.9.2 reader rule 3 (a reader at major ``R`` MUST refuse any
file at major ``M != R``).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import msgspec
from filelock import FileLock

from exlab_wizard.api.schemas import EquipmentJson, TestRunsJson
from exlab_wizard.cache import lock_path_for
from exlab_wizard.errors import SchemaMajorMismatchError
from exlab_wizard.io import atomic_write_bytes, read_msgspec_json, require_schema_major
from exlab_wizard.logging import get_logger
from exlab_wizard.utils.time import utc_now_iso

logger = get_logger(__name__)

# Re-exported for backward compatibility with downstream importers that
# previously got ``require_schema_major`` from this module. The canonical
# location is now ``exlab_wizard.io``.
__all__ = ["EquipmentCacheWriter", "require_schema_major"]


class EquipmentCacheWriter:
    """Writer for ``equipment.json`` and ``test_runs.json``.

    Instances are stateless; the class serves only to group the two
    related writers together (per §4.3 package layout). Both write
    methods are async wrappers; the actual lock + I/O work runs on a
    worker thread via ``asyncio.to_thread`` so the asyncio event loop is
    not blocked on disk syscalls.
    """

    async def write_equipment(self, path: Path, payload: EquipmentJson) -> None:
        """Write or rewrite the per-equipment ``equipment.json`` file.

        Stamping rules (§11.4.1):

        - ``last_modified_at`` is set to UTC now on every write.
        - ``first_seen_at`` is set to UTC now ONLY when the file does
          not yet exist. On a re-write of an existing file the
          ``first_seen_at`` from the on-disk version is preserved -- the
          spec is explicit that this field is never updated after the
          first write.

        The mutation runs entirely under the per-file advisory lock so
        a concurrent writer never sees the stale tempfile or a
        partially-renamed file.
        """
        await asyncio.to_thread(self._write_equipment_sync, path, payload)

    async def read_equipment(self, path: Path) -> EquipmentJson:
        """Read and decode an existing ``equipment.json`` file.

        Uses ``msgspec.json.decode(..., type=EquipmentJson)`` so schema
        validation happens in one pass with the decode. No lock is
        acquired: the writer's ``os.replace`` is atomic, so a racing
        reader sees either the pre- or post-write inode but never a
        torn file.

        Raises ``FileNotFoundError`` (from the underlying
        ``Path.read_bytes``) if the file does not exist; the caller is
        responsible for handling the absent-file case.
        """
        return await asyncio.to_thread(self._read_equipment_sync, path)

    async def write_test_runs_marker(self, path: Path, payload: TestRunsJson) -> None:
        """Write the ``test_runs.json`` marker file.

        Idempotent per §11.4.2: if the file already exists, this is a
        no-op (the on-disk content is left untouched even if ``payload``
        differs). The first write captures the project's TestRuns
        subtree as test-only; subsequent test-run creations under the
        same project leave the marker alone.

        The on-disk-existence check is performed under the per-file
        advisory lock so a concurrent first-time writer does not end up
        with two competing writes both passing the existence guard and
        racing on ``os.replace``.
        """
        await asyncio.to_thread(self._write_test_runs_marker_sync, path, payload)

    # ------------------------------------------------------------------
    # Sync helpers (run on worker thread via ``asyncio.to_thread``)
    # ------------------------------------------------------------------

    @staticmethod
    def _write_equipment_sync(path: Path, payload: EquipmentJson) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(lock_path_for(path)):
            now = utc_now_iso()
            existing_first_seen = _read_existing_first_seen_at(path)
            if existing_first_seen is not None:
                # Preserve original first_seen_at; refresh last_modified_at.
                stamped = msgspec.structs.replace(
                    payload,
                    first_seen_at=existing_first_seen,
                    last_modified_at=now,
                )
            else:
                # First write: both timestamps are "now" (matching §11.4.1
                # field semantics where first_seen_at is the wall-clock
                # at first write).
                stamped = msgspec.structs.replace(
                    payload,
                    first_seen_at=now,
                    last_modified_at=now,
                )
            atomic_write_bytes(path, msgspec.json.encode(stamped))

    @staticmethod
    def _read_equipment_sync(path: Path) -> EquipmentJson:
        return read_msgspec_json(path, EquipmentJson, expected_major=1)

    async def read_test_runs_marker(self, path: Path) -> TestRunsJson:
        """Read and decode a ``test_runs.json`` marker file.

        Raises ``SchemaMajorMismatchError`` if the file's schema major
        version exceeds the reader's supported major (1) per §11.9.2.
        """
        return await asyncio.to_thread(self._read_test_runs_marker_sync, path)

    @staticmethod
    def _read_test_runs_marker_sync(path: Path) -> TestRunsJson:
        return read_msgspec_json(path, TestRunsJson, expected_major=1)

    @staticmethod
    def _write_test_runs_marker_sync(path: Path, payload: TestRunsJson) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(lock_path_for(path)):
            if path.exists():
                # Idempotent: the marker is written once and never
                # rewritten, even if subsequent payloads differ. This
                # matches §11.4.2 verbatim.
                return
            atomic_write_bytes(path, msgspec.json.encode(payload))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _read_existing_first_seen_at(path: Path) -> str | None:
    """Return the on-disk ``first_seen_at`` if the file exists.

    Returns ``None`` when the file does not yet exist. A best-effort
    decode is used: if the file is unreadable or malformed, ``None`` is
    returned and the caller will stamp a fresh ``first_seen_at``. We
    explicitly do NOT raise here -- a corrupt registry file is recovered
    by being rewritten cleanly on the next write rather than blocking the
    operator.
    """
    if not path.exists():
        return None
    try:
        existing = read_msgspec_json(path, EquipmentJson, expected_major=1)
    except (
        msgspec.DecodeError,
        msgspec.ValidationError,
        OSError,
        SchemaMajorMismatchError,
    ) as exc:
        logger.warning(
            "equipment.json at %s could not be decoded; treating as fresh write: %s",
            path,
            exc,
        )
        return None
    return existing.first_seen_at
