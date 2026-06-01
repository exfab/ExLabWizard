"""Keep-local-aware, symlink-safe deletion of a run's staging copy.

Operator-free per-file NAS sync design (2026-05-21). Both the automatic
cleanup reaper (:meth:`exlab_wizard.sync.nas_client.NASSyncClient._delete_local`)
and the operator-facing "Clear" actions
(:func:`exlab_wizard.orchestrator.staging_clear.clear_run_dir`) must delete a
run's staging copy with **identical** semantics:

* files flagged ``keep_local`` in ``sync_state.json`` survive (the spec's
  keep-local guarantee: "excluded from cleanup deletion");
* the ``.exlab-wizard/`` metadata subtree survives so a cleared run still
  renders its files as "On NAS" tombstones;
* directory **symlinks are never descended into or removed** -- a staged run
  containing a symlink to an external directory must not have files deleted
  outside the run tree.

This module is the single shared implementation. It is a pure filesystem
helper -- no ``SyncStateWriter`` / ``api`` / ``orchestrator`` imports -- so it
can be imported from anywhere without circular-import risk. The callers own
reading the ``keep_local`` set and stamping ``cleared_at``.
"""

from __future__ import annotations

import contextlib
import fnmatch
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from exlab_wizard.constants import CACHE_DIR_NAME
from exlab_wizard.logging import get_logger

__all__ = ["CleanupCandidates", "collect_cleanup_candidates", "delete_run_files"]

_log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class CleanupCandidates:
    """Run-relative files selected or retained by cleanup planning."""

    delete: tuple[str, ...]
    retained_ignored: tuple[str, ...] = ()


def collect_cleanup_candidates(
    run_path: Path,
    *,
    keep_local: set[str],
    ignore_globs: list[str] | tuple[str, ...] = (),
    delete_ignored: bool = False,
) -> CleanupCandidates:
    """Return the run-relative files cleanup would remove.

    The walk matches :func:`delete_run_files`: the cache subtree and directory
    symlinks are not descended into, ``keep_local`` files are retained, and
    ignored files are retained unless ``delete_ignored`` explicitly opts into
    local discard.
    """
    if not run_path.exists():
        return CleanupCandidates(delete=(), retained_ignored=())

    delete: list[str] = []
    retained_ignored: list[str] = []
    for dirpath, dirnames, filenames in os.walk(run_path, followlinks=False):
        current = Path(dirpath)
        dirnames[:] = [
            d for d in dirnames if d != CACHE_DIR_NAME and not (current / d).is_symlink()
        ]
        rel_dir = current.relative_to(run_path)
        if rel_dir.parts and rel_dir.parts[0] == CACHE_DIR_NAME:
            continue
        for name in filenames:
            rel = (rel_dir / name).as_posix()
            if rel in keep_local:
                continue
            if _matches_any_glob(name, ignore_globs) and not delete_ignored:
                retained_ignored.append(rel)
                continue
            delete.append(rel)
    return CleanupCandidates(
        delete=tuple(sorted(delete)), retained_ignored=tuple(sorted(retained_ignored))
    )


def delete_run_files(
    run_path: Path,
    *,
    keep_local: set[str],
    retain_cache: bool,
    delete_only: set[str] | None = None,
    ignore_globs: list[str] | tuple[str, ...] = (),
    delete_ignored: bool = False,
) -> None:
    """Delete ``run_path`` data files honoring ``retain_cache`` and ``keep_local``.

    ``keep_local`` is a set of run-relative POSIX paths (possibly nested) that
    must survive the sweep. ``retain_cache`` keeps the ``.exlab-wizard/``
    subtree when ``True``. ``delete_only`` constrains deletion to a pre-proved
    set of run-relative files; in that mode no whole-tree recursive data delete
    is used, so files that appear after the proof survive.

    The whole-run ``shutil.rmtree`` fast path is used only when
    ``retain_cache`` is ``False`` **and** there are no ``keep_local`` files;
    any kept file (or a retained cache) forces the per-file walk so it
    survives. The walk does not follow directory symlinks -- a symlinked
    directory inside the run is left entirely untouched (neither its contents
    deleted nor the link removed).

    Idempotent: a missing ``run_path`` is a no-op.
    """
    if not run_path.exists():
        return
    if delete_only is None and not retain_cache and not keep_local and not ignore_globs:
        # No retained files and no cache to preserve: drop the whole run.
        # ``rmtree`` does not follow the top-level dir if it is itself a
        # symlink (it raises) -- a run dir is always a real directory here.
        shutil.rmtree(run_path, ignore_errors=True)
        return

    planned = collect_cleanup_candidates(
        run_path,
        keep_local=keep_local,
        ignore_globs=ignore_globs,
        delete_ignored=delete_ignored,
    )
    delete_set = set(planned.delete) if delete_only is None else set(delete_only)

    # Per-file walk: delete every file that is neither under
    # ``.exlab-wizard/`` nor flagged ``keep_local``. ``followlinks=False``
    # (the os.walk default, made explicit) keeps the walk inside the run
    # tree -- a symlinked subdirectory is yielded as a name but never
    # descended into, so its target's contents are never touched.
    for dirpath, dirnames, filenames in os.walk(run_path, followlinks=False):
        current = Path(dirpath)
        # Do not descend into the cache subtree or any symlinked directory.
        dirnames[:] = [
            d for d in dirnames if d != CACHE_DIR_NAME and not (current / d).is_symlink()
        ]
        rel_dir = current.relative_to(run_path)
        if rel_dir.parts and rel_dir.parts[0] == CACHE_DIR_NAME:
            continue
        for name in filenames:
            entry = current / name
            rel = (rel_dir / name).as_posix()
            if rel in keep_local:
                continue
            if rel not in delete_set:
                continue
            # A file that is itself a symlink: unlink the link only (never
            # the target). ``unlink`` does exactly that.
            with contextlib.suppress(OSError):
                entry.unlink()
    if not retain_cache:
        shutil.rmtree(run_path / CACHE_DIR_NAME, ignore_errors=True)
    _prune_empty_dirs(run_path)
    if not retain_cache:
        with contextlib.suppress(OSError):
            run_path.rmdir()


def _prune_empty_dirs(run_path: Path) -> None:
    """Remove now-empty real directories under ``run_path`` (deepest first).

    The run directory itself, the ``.exlab-wizard/`` cache subtree, and any
    symlinked directory are never removed. A real directory left empty by the
    delete walk is pruned so cleanup leaves only retained files and metadata.
    """
    cache_dir_path = run_path / CACHE_DIR_NAME
    real_dirs: list[Path] = []
    for dirpath, dirnames, _filenames in os.walk(run_path, followlinks=False):
        current = Path(dirpath)
        # Prune symlinked directories from the descent so we never rmdir one.
        dirnames[:] = [d for d in dirnames if not (current / d).is_symlink()]
        if current != run_path:
            real_dirs.append(current)
    # Deepest first so a parent emptied by pruning its children is itself
    # prunable in the same pass.
    for directory in sorted(real_dirs, key=lambda p: len(p.parts), reverse=True):
        if directory == cache_dir_path or cache_dir_path in directory.parents:
            continue
        with contextlib.suppress(OSError):
            if not any(directory.iterdir()):
                directory.rmdir()


def _matches_any_glob(name: str, globs: list[str] | tuple[str, ...]) -> bool:
    """Return True if ``name`` matches any configured ignore glob."""
    return any(fnmatch.fnmatch(name, pattern) for pattern in globs)
