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
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import msgspec
from filelock import FileLock

from exlab_wizard.api.schemas import EquipmentJson, TestRunsJson
from exlab_wizard.errors import SchemaMajorMismatchError
from exlab_wizard.logging import get_logger

logger = get_logger(__name__)

__all__ = ["EquipmentCacheWriter"]


# Suffix used for the per-file advisory lock that ``filelock`` creates
# alongside the protected file. Matches the convention in §4.4.5.
_LOCK_SUFFIX: str = ".lock"

# Suffix used for the temp file written before atomic ``os.replace``.
_TMP_SUFFIX: str = ".tmp"


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
        lock_path = str(path) + _LOCK_SUFFIX
        with FileLock(lock_path):
            now = _utc_now_iso()
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
            _atomic_write_bytes(path, msgspec.json.encode(stamped))

    @staticmethod
    def _read_equipment_sync(path: Path) -> EquipmentJson:
        data = path.read_bytes()
        _check_schema_major(data, expected_major=1)
        return msgspec.json.decode(data, type=EquipmentJson)

    async def read_test_runs_marker(self, path: Path) -> TestRunsJson:
        """Read and decode a ``test_runs.json`` marker file.

        Raises ``SchemaMajorMismatchError`` if the file's schema major
        version exceeds the reader's supported major (1) per §11.9.2.
        """
        return await asyncio.to_thread(self._read_test_runs_marker_sync, path)

    @staticmethod
    def _read_test_runs_marker_sync(path: Path) -> TestRunsJson:
        data = path.read_bytes()
        _check_schema_major(data, expected_major=1)
        return msgspec.json.decode(data, type=TestRunsJson)

    @staticmethod
    def _write_test_runs_marker_sync(path: Path, payload: TestRunsJson) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = str(path) + _LOCK_SUFFIX
        with FileLock(lock_path):
            if path.exists():
                # Idempotent: the marker is written once and never
                # rewritten, even if subsequent payloads differ. This
                # matches §11.4.2 verbatim.
                return
            _atomic_write_bytes(path, msgspec.json.encode(payload))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _check_schema_major(data: bytes, *, expected_major: int) -> None:
    """Inspect the on-disk ``schema_version`` and raise on a major mismatch.

    Spec §11.9.2 reader rule 3: a reader at major ``R`` MUST refuse any file
    at major ``M != R`` with a structured error. We surface the error as
    ``SchemaMajorMismatchError`` carrying ``expected_major`` and ``found``
    so the caller can report it via the §4.6.3 error envelope.
    """
    try:
        head = msgspec.json.decode(data, type=dict)
    except (msgspec.DecodeError, msgspec.ValidationError):
        # Malformed JSON is not a major-mismatch case; let downstream
        # decoders surface the precise validation error.
        return
    version = str(head.get("schema_version", ""))
    if not version:
        return
    major_part = version.split(".", 1)[0]
    try:
        major = int(major_part)
    except ValueError:
        return
    if major != expected_major:
        raise SchemaMajorMismatchError(expected_major=expected_major, found=version)


def _utc_now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string with ``Z`` suffix.

    Matches the timestamp format used by the cache schemas (§11.3, §11.4.1,
    §11.4.2). Seconds-resolution is sufficient -- subsecond precision is
    not part of any cross-component contract.
    """
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


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
        existing = msgspec.json.decode(path.read_bytes(), type=EquipmentJson)
    except (msgspec.DecodeError, msgspec.ValidationError, OSError) as exc:
        logger.warning(
            "equipment.json at %s could not be decoded; treating as fresh write: %s",
            path,
            exc,
        )
        return None
    return existing.first_seen_at


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically.

    Implementation follows the §4.4.5 recipe: write to a sibling temp
    file in the same directory, ``fsync`` so the bytes are durable on
    disk, then ``os.replace`` for the atomic rename. Same-directory
    placement guarantees the rename is a single inode-table update on
    every supported filesystem (POSIX rename(2); Windows MoveFileEx with
    MOVEFILE_REPLACE_EXISTING).

    The temp file uses a ``.tmp`` suffix on the target name so audit
    tools that walk the cache directory can recognize the transient
    artifact if a process crashes mid-write.
    """
    parent = path.parent
    # NamedTemporaryFile gives us a unique name; we still write to it
    # ourselves so we can fsync before replace.
    fd, tmp_name = tempfile.mkstemp(
        dir=str(parent),
        prefix=path.name + ".",
        suffix=_TMP_SUFFIX,
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        # Best-effort cleanup; ignore any error so the original failure
        # surfaces.
        with contextlib.suppress(OSError):
            tmp_path.unlink()
        raise
