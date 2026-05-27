"""``msgspec.Struct`` types for the per-run ``sync_state.json`` cache.

Operator-free per-file NAS sync design (2026-05-21). These structs live in
the dependency-free ``cache`` package -- not ``api.schemas`` -- so the
``cache``, ``sync``, and ``orchestrator`` modules can import them (and
:class:`~exlab_wizard.cache.sync_state_writer.SyncStateWriter`) at module
scope without re-entering the ``api`` package and forming a circular
import. ``api.schemas`` re-exports both names so the wider "single source
of truth for cache schemas" surface is preserved for API callers.
"""

from __future__ import annotations

from msgspec import Struct

__all__ = ["FileSyncRecord", "SyncStateJson"]


class FileSyncRecord(
    Struct,
    omit_defaults=True,
    forbid_unknown_fields=False,
):
    """Per-file sync record stored under ``sync_state.json``'s ``files`` map.

    One record per run-relative POSIX path. These records are *freely
    mutated in place* by ``SyncStateWriter`` as a file is synced,
    re-modified, and re-synced.

    * ``synced_signature`` -- the ``(st_size, st_mtime_ns)`` captured at the
      last successful sync. ``None`` means the file has never synced. A file
      whose current signature differs is "modified since sync" and becomes
      eligible again once it re-settles.
    * ``verified_at`` -- the ISO-8601 timestamp at which the file's SHA-256
      was confirmed against the NAS copy. ``None`` until verified; a file
      counts toward the ``SYNCED`` rollup only once this is set.
    * ``keep_local`` -- when ``True`` the file still syncs to the NAS but is
      excluded from cleanup deletion.
    * ``verified_sha256`` -- the SHA-256 hex digest of the local bytes
      captured at sync time. Slot A of the 2026-05-26 rclone-only
      migration: ``rclone check --download --combined`` confirms the NAS
      copy matches the local source, and we record the local SHA here so
      the offline-audit affordance lost by dropping ``checksums.sha256``
      is preserved without any wire cost. ``None`` for records that
      pre-date the migration or for files synced via the legacy verify
      path; reads must tolerate its absence.
    """

    synced_signature: tuple[int, int] | None = None
    verified_at: str | None = None
    keep_local: bool = False
    verified_sha256: str | None = None


class SyncStateJson(
    Struct,
    omit_defaults=True,
    forbid_unknown_fields=False,
):
    """``sync_state.json`` per-run sync-state record at schema version 1.0.

    Written by the orchestrator only. A *freely-mutable current-state map*
    (not append-only): ``files`` maps a run-relative POSIX path to its
    :class:`FileSyncRecord`. ``cleared_at`` is set once the run's staging
    copy has been cleaned up. The run-level ``SYNCING``/``SYNCED``/``CLEARED``
    rollup is derived on read from this payload, never persisted as such.
    """

    schema_version: str
    cleared_at: str | None = None
    files: dict[str, FileSyncRecord] = {}
