"""Atomic reader/writer for ``creation.json``. Backend Spec §4.4.5.

All ``creation.json`` mutations in the codebase MUST go through
:class:`CreationWriter`. The class enforces the four invariants of
§4.4.5:

1. **Typed encode/decode** via ``msgspec.json`` against the
   :class:`~exlab_wizard.api.schemas.CreationJson` Struct hierarchy
   (no stdlib ``json``, no separate Pydantic round-trip).
2. **Tempfile + ``os.replace``** for every write (atomic on POSIX,
   atomic-on-same-volume on Windows).
3. **Per-file advisory file lock**. Writes use ``filelock.FileLock``
   (exclusive); reads use ``filelock.ReadWriteLock`` in read mode
   (shared) so concurrent readers do not block each other.
4. **Lock-for-full-cycle** on mutations: the read, mutator-apply,
   and write all happen inside a single exclusive lock acquisition
   so two writers cannot lost-update each other.

Forward-compat: unknown fields encountered on read are preserved on
write per §11.9.3 writer-policy rule 2. The writer keeps the raw
``dict[str, Any]`` decoded form alongside the typed struct, and
merges any keys that were not consumed by the Struct decoder back
into the encoded output.

Backward-compat: when the file's ``schema_version`` is older than the
writer's current version, the documented defaults from §11.3's history
table are applied during decode and the next mutation rewrites the
file at the writer's current version (§11.9.3 rule 3). Major-version
mismatches raise :class:`~exlab_wizard.errors.SchemaMajorMismatchError`
per §11.9.2 rule 3.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import msgspec
from filelock import FileLock, ReadWriteLock
from msgspec import json as msgspec_json

from exlab_wizard.api.schemas import CreationJson
from exlab_wizard.cache import lock_path_for
from exlab_wizard.cache.equipment import require_schema_major
from exlab_wizard.constants import CREATION_JSON_VERSION, LIMSProjectSource, RunKind
from exlab_wizard.io import atomic_write_bytes
from exlab_wizard.logging import get_logger
from exlab_wizard.utils.time import parse_utc_iso_or_none

__all__ = [
    "CreationWriter",
    "select_active_overrides",
]

_log = get_logger(__name__)

# Reader's expected major version (the writer always emits this major).
_READER_MAJOR: int = int(CREATION_JSON_VERSION.split(".", 1)[0])


# ---------------------------------------------------------------------------
# Pure helpers (exported for tests + sync-side reuse)
# ---------------------------------------------------------------------------


def select_active_overrides(
    validation_overrides: Iterable[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Return the subset of override entries that are currently active.

    Implements the matching algorithm in spec §11.3:

    1. Build a set ``revoked_ids`` of every entry's ``revokes`` pointer
       where ``entry.revoked == True``.
    2. An override entry is active iff ``entry.revoked == False``,
       its ``id`` is not in ``revoked_ids``, and its ``expires_at`` is
       either absent/``None`` or strictly greater than ``now``.

    ``now`` defaults to the current UTC time. Pass an explicit value for
    deterministic tests. Tombstones whose ``revokes`` target is missing
    from the array are logged at WARN and otherwise have no effect.
    """
    entries = list(validation_overrides)
    if now is None:
        now = datetime.now(tz=UTC)

    # Build set of revoked override ids and the set of ids that are present.
    present_ids: set[str] = {e["id"] for e in entries if "id" in e}
    revoked_ids: set[str] = set()
    for entry in entries:
        if not entry.get("revoked", False):
            continue
        target = entry.get("revokes")
        if target is None:
            continue
        if target not in present_ids:
            _log.warning(
                "tombstone references unknown override id (orphan): id=%s revokes=%s",
                entry.get("id"),
                target,
            )
            continue
        revoked_ids.add(target)

    active: list[dict[str, Any]] = []
    for entry in entries:
        if entry.get("revoked", False):
            continue
        if entry.get("id") in revoked_ids:
            continue
        expires_at = entry.get("expires_at")
        if expires_at is not None and not _is_future(expires_at, now):
            continue
        active.append(entry)
    return active


def _is_future(expires_at: str, now: datetime) -> bool:
    """Return True if ``expires_at`` (UTC ISO-8601) is strictly after ``now``.

    The spec uses a naive "wall-clock UTC" comparison; we accept the two
    common forms (``2026-04-17T14:32:00Z`` and the explicit
    ``+00:00`` offset) and reject anything else as "expired now" so a
    malformed value re-engages the gate (fail-safe).
    """
    parsed = parse_utc_iso_or_none(expires_at)
    if parsed is None:
        _log.warning("override expires_at is malformed; treating as expired: %r", expires_at)
        return False
    return parsed > now


# ---------------------------------------------------------------------------
# Schema-version migration helpers (§11.3 history; §11.9.2 reader policy)
# ---------------------------------------------------------------------------


def _apply_migration_defaults(raw: dict[str, Any]) -> dict[str, Any]:
    """Apply §11.3 history-table defaults for fields missing on old minors.

    Returns the same dict (mutated in place) so the caller can keep a
    single reference. Order of the defaults below mirrors the history
    table in §11.3:

    * 1.0 -> 1.1: ``run_kind`` defaults to ``"experimental"``.
    * 1.1 -> 1.2: ``validation_overrides`` defaults to ``[]``.
    * 1.2 -> 1.3: ``plugins_applied[].isolation`` is treated as absent
      when missing -- the field is nullable, no default needed.
    * 1.4: silently dropped ``lims_status`` field (v1.4 was retracted;
      see §11.3 history). No reader action required here -- the field
      simply isn't in the Struct, so msgspec discards it.
    * 1.4 -> 1.5: ``lims_project`` defaults to *absent* (stays missing
      if the reader is at >= 1.5 and the file is older). Project- and
      run-level files at older versions are exempt from the soft-tier
      "missing required field" finding (§11.3 paragraph 9).
    * 1.5 -> 1.6: ``validation_overrides[].id`` defaults to a freshly
      generated UUID v4 for entries that don't carry one (rare;
      pre-1.6 files in production are unusual).
    * 1.6 -> 1.7: ``validation_overrides[].expires_at`` defaults to
      ``None`` -- nullable, no default needed.
    * 1.7 -> 1.8: ``lims_project.source`` defaults to ``"live"``,
      ``lims_project.cache_freshness_at_use`` defaults to ``None``.
    """
    raw.setdefault("run_kind", RunKind.EXPERIMENTAL.value)
    raw.setdefault("validation_overrides", [])
    # Backfill UUIDs on override entries that are missing one.
    for entry in raw.get("validation_overrides", []):
        if "id" not in entry:
            entry["id"] = str(uuid.uuid4())
    # Backfill lims_project subfields for files predating 1.8.
    lims_project = raw.get("lims_project")
    if isinstance(lims_project, dict):
        lims_project.setdefault("source", LIMSProjectSource.LIVE.value)
        lims_project.setdefault("cache_freshness_at_use", None)
    return raw


# ---------------------------------------------------------------------------
# CreationWriter
# ---------------------------------------------------------------------------


class CreationWriter:
    """Atomic reader/writer for ``creation.json``. Backend Spec §4.4.5."""

    def __init__(self, lock_timeout_seconds: float = 30.0) -> None:
        """Construct the writer.

        ``lock_timeout_seconds`` is the wall-clock cap for acquiring the
        per-file lock; exceeding it raises ``filelock.Timeout`` so the
        caller can react instead of hanging forever. The default of
        30 seconds aligns with the quit-coordinator's drain budget
        (§4.3.2 step 2).
        """
        self._lock_timeout = lock_timeout_seconds

    # -- Public async API ---------------------------------------------------

    async def write_creation(self, path: Path, payload: CreationJson) -> None:
        """Write a fresh ``creation.json``. Reserved for initial creation.

        Acquires the per-file exclusive lock defensively even though no
        prior file is expected; that way two simultaneous "first writers"
        serialize correctly and the second one observes a now-existing
        file (which the controller treats as a conflict).
        """
        await asyncio.to_thread(self._write_creation_sync, path, payload)

    async def update_creation_atomic(
        self,
        path: Path,
        mutator: Callable[[CreationJson], CreationJson],
    ) -> CreationJson:
        """Read, mutate, and write ``creation.json`` under one ``LOCK_EX``.

        The full read-mutator-write cycle happens inside a single
        ``filelock.FileLock`` acquisition. Two concurrent
        ``update_creation_atomic`` calls on the same path serialize:
        the second waits for the first to release before it reads,
        so neither lost-updates the other.

        The mutator is allowed to either mutate the struct in place
        and return it, or return a fresh struct.
        """
        return await asyncio.to_thread(self._update_creation_sync, path, mutator)

    async def read_creation_snapshot(self, path: Path) -> CreationJson:
        """Read a snapshot under ``LOCK_SH`` (shared/read lock).

        Concurrent readers do not block each other; an ``LOCK_EX``
        writer waits for active readers to release. Use this when you
        need a typed view of the file but do not intend to mutate.
        """
        return await asyncio.to_thread(self._read_creation_sync, path)

    # -- Synchronous core ---------------------------------------------------

    def _write_creation_sync(self, path: Path, payload: CreationJson) -> None:
        lock = FileLock(lock_path_for(path), timeout=self._lock_timeout)
        with lock:
            self._encode_and_replace(path, _payload_to_dict(payload))

    def _update_creation_sync(
        self,
        path: Path,
        mutator: Callable[[CreationJson], CreationJson],
    ) -> CreationJson:
        lock = FileLock(lock_path_for(path), timeout=self._lock_timeout)
        with lock:
            raw = self._decode_raw(path)
            self._reject_major_mismatch(raw)
            _apply_migration_defaults(raw)

            payload = msgspec.convert(raw, type=CreationJson)
            new_payload = mutator(payload)
            new_dict = _payload_to_dict(new_payload)
            # Preserve any unknown fields the typed Struct dropped during
            # convert(). This is §11.9.3 writer-policy rule 2.
            _merge_unknown_fields(new_dict, raw)
            self._encode_and_replace(path, new_dict)
            return new_payload

    def _read_creation_sync(self, path: Path) -> CreationJson:
        rwlock = ReadWriteLock(lock_path_for(path), timeout=self._lock_timeout)
        with rwlock.read_lock():
            raw = self._decode_raw(path)
            self._reject_major_mismatch(raw)
            _apply_migration_defaults(raw)
            return msgspec.convert(raw, type=CreationJson)

    # -- Internal helpers ---------------------------------------------------

    def _decode_raw(self, path: Path) -> dict[str, Any]:
        """Load the file as a raw ``dict`` (no Struct conversion yet).

        We need the raw shape so that
        :func:`_apply_migration_defaults` can backfill old-minor fields
        BEFORE the struct decoder runs (otherwise the missing fields
        would surface as required-field errors).
        """
        return msgspec_json.decode(path.read_bytes(), type=dict[str, Any])

    def _reject_major_mismatch(self, raw: dict[str, Any]) -> None:
        version = raw.get("schema_version")
        if not isinstance(version, str):
            version = str(version) if version is not None else ""
        require_schema_major(version, expected_major=_READER_MAJOR)

    def _encode_and_replace(self, path: Path, payload: dict[str, Any]) -> None:
        """Encode ``payload`` to bytes, write atomically via atomic_write_bytes.

        The writer pins ``schema_version`` to the current
        :data:`CREATION_JSON_VERSION` on every write -- §11.9.3 rule 3.
        """
        payload["schema_version"] = CREATION_JSON_VERSION
        encoded = msgspec_json.encode(payload)
        atomic_write_bytes(path, encoded)


# ---------------------------------------------------------------------------
# Module-private helpers
# ---------------------------------------------------------------------------


def _payload_to_dict(payload: CreationJson) -> dict[str, Any]:
    """Recursively convert a ``CreationJson`` into a plain ``dict``.

    Uses :func:`msgspec.to_builtins` so nested Structs (``LimsProjectBlock``,
    ``TemplateBlock``, ...) are serialized too. ``omit_defaults=True`` on
    the Struct types is honored, so the output is the compact shape we
    want on disk.
    """
    return msgspec.to_builtins(payload)


def _merge_unknown_fields(new_dict: dict[str, Any], raw: dict[str, Any]) -> None:
    """Copy keys from ``raw`` that are NOT in ``new_dict`` back into it.

    This is how forward-compat (§11.9.3 rule 2) is preserved: a v0.7
    writer mutating a file written by a v0.8 writer keeps the v0.8
    fields. Only top-level keys are merged here -- the spec's history
    table tracks additions at the top level (e.g. ``lims_project`` was
    added at the top of the schema in 1.5). Unknown keys nested inside
    typed sub-structs are NOT preserved and the spec accepts that
    trade-off; sub-blocks are versioned alongside the top-level
    ``schema_version`` field.
    """
    for key, value in raw.items():
        if key not in new_dict:
            new_dict[key] = value
