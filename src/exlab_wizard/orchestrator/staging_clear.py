"""Operator-facing staging-clear helper. Backend Spec §13.7.

The operator-free per-file NAS sync redesign (2026-05-21) removed the
``orchestrator.cleanup`` module and the ``ingest.json`` state machine. Two
call sites -- the ``/staging`` router and the NiceGUI mount -- delete a
staged run's local copy on operator request ("Clear" / "Clear verified
runs").

Phase 5: an operator clear must have the **same** keep-local-aware
semantics as the automatic cleanup reaper
(:meth:`exlab_wizard.sync.nas_client.NASSyncClient._delete_local`). Both
delegate to the shared :func:`exlab_wizard.sync.run_delete.delete_run_files`
helper, so a ``keep_local`` file survives an operator clear and the
``.exlab-wizard/`` metadata subtree (incl. ``sync_state.json``) is retained
-- a cleared run still renders its files as "On NAS" tombstones. After the
delete, ``cleared_at`` is stamped so the run's rollup flips to ``CLEARED``.
"""

from __future__ import annotations

from pathlib import Path

from exlab_wizard.cache.sync_state_writer import SyncStateWriter
from exlab_wizard.logging import get_logger
from exlab_wizard.orchestrator._scan import count_files_and_bytes
from exlab_wizard.sync.run_delete import delete_run_files

__all__ = ["clear_run_dir"]

_log = get_logger(__name__)


def clear_run_dir(run_path: Path) -> tuple[int, int]:
    """Delete a staged run's data files; return ``(file_count, bytes_freed)``.

    Idempotent: a missing directory returns ``(0, 0)``.

    Keep-local-aware (Phase 5): files flagged ``keep_local`` in the run's
    ``sync_state.json`` survive, the ``.exlab-wizard/`` metadata subtree is
    retained, and directory symlinks are never descended into or removed --
    identical semantics to the automatic cleanup reaper. After deletion,
    ``cleared_at`` is stamped in ``sync_state.json`` so the run rolls up to
    ``CLEARED`` (and still lists "On NAS" tombstones).

    The reported ``(file_count, bytes_freed)`` counts the files actually
    removed -- i.e. excludes the cache subtree and any retained ``keep_local``
    files.
    """
    if not run_path.exists():
        return 0, 0

    writer = SyncStateWriter()
    state = writer.read_sync(run_path)
    keep_local = {rel for rel, rec in state.files.items() if rec.keep_local}

    # Count only the files that will actually be removed: total run files
    # (excluding the cache subtree) minus the retained keep-local files.
    file_count, bytes_freed = count_files_and_bytes(run_path, exclude_cache=True)
    for rel in keep_local:
        kept = run_path / rel
        try:
            stat = kept.stat()
        except OSError:
            continue
        file_count -= 1
        bytes_freed -= stat.st_size

    delete_run_files(run_path, keep_local=keep_local, retain_cache=True)
    # Stamp ``cleared_at`` so the run rolls up to CLEARED. ``retain_cache``
    # is forced True above, so the cache subtree (and the record) survive.
    writer.mark_cleared_sync(run_path)

    _log.info(
        "staging cleared: path=%s files=%d bytes_freed=%d keep_local=%d",
        run_path,
        max(file_count, 0),
        max(bytes_freed, 0),
        len(keep_local),
    )
    return max(file_count, 0), max(bytes_freed, 0)
