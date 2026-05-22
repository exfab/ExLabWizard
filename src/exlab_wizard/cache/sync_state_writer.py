"""Orchestrator-only writer for ``sync_state.json``.

Operator-free per-file NAS sync design (2026-05-21). The orchestrator
writes one ``sync_state.json`` per run pending NAS sync, under
``<run>/.exlab-wizard/sync_state.json``. It is a **freely-mutable
current-state map**: per-file records are overwritten in place as a file
is synced, re-modified, and re-synced, and ``cleared_at`` is stamped once
when the run's staging copy is cleaned up.

Disk-side guarantees follow the §4.4.5 ``CacheWriter`` contract:

* ``msgspec.json`` for typed encode/decode (schema validation in one pass).
* ``filelock.FileLock`` advisory exclusive lock around every
  read-mutate-write cycle so concurrent updates never lose a record.
* Atomic write via tempfile + ``fsync`` + ``os.replace``
  (:func:`~exlab_wizard.io.atomic_write_bytes`).

The run-level ``SYNCING`` / ``SYNCED`` / ``CLEARED`` rollup is **derived on
read** by :meth:`SyncStateWriter.rollup_state` -- it is never persisted,
because it can oscillate (a ``SYNCED`` run whose file is modified again
returns to ``SYNCING``).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import msgspec
from filelock import FileLock

from exlab_wizard.cache import lock_path_for
from exlab_wizard.cache.sync_state_schema import FileSyncRecord, SyncStateJson
from exlab_wizard.constants import (
    SYNC_STATE_FILENAME,
    SYNC_STATE_JSON_VERSION,
    RunSyncState,
)
from exlab_wizard.io import atomic_write_bytes, read_msgspec_json
from exlab_wizard.logging import get_logger
from exlab_wizard.paths import cache_dir
from exlab_wizard.utils.time import utc_now_iso

__all__ = ["SyncStateWriter"]

_logger = get_logger(__name__)

# Reader's expected major version (every writer always emits this major).
_EXPECTED_MAJOR: int = int(SYNC_STATE_JSON_VERSION.split(".", 1)[0])


def _sync_state_path(run_path: Path) -> Path:
    """Return the ``sync_state.json`` path under a run directory."""
    return cache_dir(run_path) / SYNC_STATE_FILENAME


def _ensure_cache_dir(path: Path) -> None:
    """Create ``path``'s parent (the run's ``.exlab-wizard/`` cache) dir.

    Neither :func:`atomic_write_bytes` nor :class:`filelock.FileLock`
    creates the parent directory, so a mutator targeting a run that has
    no ``.exlab-wizard/`` dir yet would raise (and surface as an HTTP
    500). Every blocking mutator calls this first so a missing cache dir
    can never fault the write. Idempotent.
    """
    path.parent.mkdir(parents=True, exist_ok=True)


def _empty_state() -> SyncStateJson:
    """Return a fresh, file-less :class:`SyncStateJson` at the current version."""
    return SyncStateJson(schema_version=SYNC_STATE_JSON_VERSION)


class SyncStateWriter:
    """Writer for ``sync_state.json``. Orchestrator-mode only.

    All public methods are ``async`` to match the §4.4.5 ``CacheWriter``
    contract; the blocking lock + I/O work is dispatched through
    ``asyncio.to_thread`` so the FastAPI event loop is never blocked.
    """

    async def read(self, run_path: Path) -> SyncStateJson:
        """Read and decode the run's ``sync_state.json``.

        Returns a fresh empty :class:`SyncStateJson` when the file does not
        exist yet -- a run with no sync activity simply has no record. Raises
        ``SchemaMajorMismatchError`` (§11.9.2) when the on-disk file carries
        a different schema major than ``SYNC_STATE_JSON_VERSION``.
        """
        return await asyncio.to_thread(self._read_blocking, run_path)

    def read_sync(self, run_path: Path) -> SyncStateJson:
        """Blocking variant of :meth:`read` for synchronous callers.

        :meth:`read` dispatches the blocking lock + decode through
        ``asyncio.to_thread``. This variant runs the lock + decode inline;
        synchronous read-side code -- notably
        :func:`exlab_wizard.orchestrator.staging_query.list_staged_runs` and
        :func:`exlab_wizard.orchestrator.staging_clear.clear_run_dir` -- calls
        it directly. Some of those call sites run inside a ``@ui.page``
        handler (i.e. on the event loop); the lock + a single small JSON
        decode is brief enough not to matter there, and the read-side query
        is itself synchronous, so there is no ``to_thread`` hop to make.
        Returns a fresh empty :class:`SyncStateJson` when the file is absent.
        """
        return self._read_blocking(run_path)

    async def upsert_file(
        self,
        run_path: Path,
        rel_path: str,
        *,
        synced_signature: tuple[int, int] | None = None,
        verified_at: str | None = None,
    ) -> SyncStateJson:
        """Create or update one file record under an exclusive lock.

        The record for ``rel_path`` (a run-relative POSIX path) is created if
        absent, otherwise updated in place. ``synced_signature`` and
        ``verified_at`` overwrite the record's fields; the record's other
        fields (notably ``keep_local``) are preserved.
        """
        return await asyncio.to_thread(
            self._upsert_file_blocking,
            run_path,
            rel_path,
            synced_signature,
            verified_at,
        )

    async def set_keep_local(
        self,
        run_path: Path,
        rel_path: str,
        value: bool,
    ) -> SyncStateJson:
        """Toggle a file's ``keep_local`` flag, creating the record if absent."""
        return await asyncio.to_thread(
            self._set_keep_local_blocking,
            run_path,
            rel_path,
            value,
        )

    async def mark_cleared(self, run_path: Path) -> SyncStateJson:
        """Stamp ``cleared_at`` with the current UTC time.

        Called once the run's staging copy has been cleaned up; this flips
        the derived rollup to ``CLEARED``.
        """
        return await asyncio.to_thread(self._mark_cleared_blocking, run_path)

    def mark_cleared_sync(self, run_path: Path) -> SyncStateJson:
        """Blocking variant of :meth:`mark_cleared` for synchronous callers.

        Used by the synchronous :func:`clear_run_dir` operator-clear path so
        an operator "Clear" stamps ``cleared_at`` exactly like the automatic
        cleanup reaper does. No-op-safe when ``sync_state.json`` is absent --
        a record is created carrying only ``cleared_at``.
        """
        return self._mark_cleared_blocking(run_path)

    @staticmethod
    def rollup_state(state: SyncStateJson) -> RunSyncState:
        """Derive the run-level :class:`RunSyncState` rollup from ``state``.

        Pure function -- no I/O, no mutation. The rollup is computed on read
        rather than persisted because the ``SYNCING``/``SYNCED`` distinction
        oscillates as files are re-modified.

        * ``CLEARED`` -- ``cleared_at`` is set (takes precedence even if some
          files are unverified, e.g. ``keep_local`` files left on disk).
        * ``SYNCED`` -- ``files`` is non-empty and every record has a non-null
          ``verified_at``.
        * ``SYNCING`` -- otherwise (no files tracked yet, or at least one file
          still unverified).
        """
        if state.cleared_at is not None:
            return RunSyncState.CLEARED
        if state.files and all(rec.verified_at is not None for rec in state.files.values()):
            return RunSyncState.SYNCED
        return RunSyncState.SYNCING

    # ---- Blocking helpers (run via asyncio.to_thread) ---------------------

    def _read_blocking(self, run_path: Path) -> SyncStateJson:
        path = _sync_state_path(run_path)
        with FileLock(lock_path_for(path)):
            return self._decode_locked(path)

    @staticmethod
    def _decode_locked(path: Path) -> SyncStateJson:
        """Decode ``sync_state.json``, returning an empty state if absent.

        Caller MUST already hold the per-file ``FileLock``.
        """
        if not path.exists():
            return _empty_state()
        return read_msgspec_json(path, SyncStateJson, expected_major=_EXPECTED_MAJOR)

    def _upsert_file_blocking(
        self,
        run_path: Path,
        rel_path: str,
        synced_signature: tuple[int, int] | None,
        verified_at: str | None,
    ) -> SyncStateJson:
        path = _sync_state_path(run_path)
        _ensure_cache_dir(path)
        with FileLock(lock_path_for(path)):
            payload = self._decode_locked(path)
            existing = payload.files.get(rel_path, FileSyncRecord())
            record = msgspec.structs.replace(
                existing,
                synced_signature=synced_signature,
                verified_at=verified_at,
            )
            new_payload = self._with_file(payload, rel_path, record)
            atomic_write_bytes(path, msgspec.json.encode(new_payload))
        _logger.info(
            "sync_state.json upsert: %s (file=%s, verified=%s)",
            path,
            rel_path,
            verified_at is not None,
        )
        return new_payload

    def _set_keep_local_blocking(
        self,
        run_path: Path,
        rel_path: str,
        value: bool,
    ) -> SyncStateJson:
        path = _sync_state_path(run_path)
        _ensure_cache_dir(path)
        with FileLock(lock_path_for(path)):
            payload = self._decode_locked(path)
            existing = payload.files.get(rel_path, FileSyncRecord())
            record = msgspec.structs.replace(existing, keep_local=value)
            new_payload = self._with_file(payload, rel_path, record)
            atomic_write_bytes(path, msgspec.json.encode(new_payload))
        _logger.info(
            "sync_state.json keep_local: %s (file=%s, value=%s)",
            path,
            rel_path,
            value,
        )
        return new_payload

    def _mark_cleared_blocking(self, run_path: Path) -> SyncStateJson:
        path = _sync_state_path(run_path)
        _ensure_cache_dir(path)
        with FileLock(lock_path_for(path)):
            payload = self._decode_locked(path)
            new_payload = msgspec.structs.replace(payload, cleared_at=utc_now_iso())
            atomic_write_bytes(path, msgspec.json.encode(new_payload))
        _logger.info("sync_state.json marked cleared: %s", path)
        return new_payload

    @staticmethod
    def _with_file(
        payload: SyncStateJson,
        rel_path: str,
        record: FileSyncRecord,
    ) -> SyncStateJson:
        """Return a copy of ``payload`` with ``files[rel_path]`` set to ``record``.

        Builds a fresh ``files`` mapping so the input payload is never
        mutated in place.
        """
        new_files = {**payload.files, rel_path: record}
        return msgspec.structs.replace(payload, files=new_files)
