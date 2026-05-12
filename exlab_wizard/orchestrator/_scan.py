"""Shared filesystem helpers for the orchestrator. Backend Spec §13.2.

Both :mod:`staging_query` and :mod:`staging_watcher` need to walk
``staging_root`` and discover run leaves; both also need to count files
and bytes under a run directory. Centralising these helpers keeps the
two modules in sync and avoids subtle drift in path conventions.
"""

from __future__ import annotations

import os
from pathlib import Path

from exlab_wizard.constants import (
    CACHE_DIR_NAME,
    TEST_RUNS_DIR_NAME,
)
from exlab_wizard.paths import is_run_dir, is_test_run_dir

__all__ = [
    "count_files_and_bytes",
    "iter_subdirs",
    "walk_run_leaves",
]


def iter_subdirs(parent: Path) -> list[Path]:
    """Return immediate sub-directories of ``parent``.

    Skips ``.exlab-wizard/``, hidden dot-files, and entries that race out
    of existence between the scan and the stat. Symlinks are not
    followed (the staging tree is rooted on the orchestrator's local
    disk; deep links into the equipment machine's filesystem would be a
    security smell).
    """
    out: list[Path] = []
    try:
        entries = list(os.scandir(parent))
    except (FileNotFoundError, NotADirectoryError, PermissionError):
        return out
    for entry in entries:
        if entry.name == CACHE_DIR_NAME or entry.name.startswith("."):
            continue
        try:
            if entry.is_dir(follow_symlinks=False):
                out.append(Path(entry.path))
        except OSError:
            continue
    return out


def walk_run_leaves(staging_root: Path) -> list[Path]:
    """Return every ``Run_*`` / ``TestRun_*`` directory under ``staging_root``.

    Per §13.2 the staging layout is
    ``<staging_root>/<EQUIP>/<PROJ>/<run_or_TestRuns>``; experimental
    runs sit directly under the project, test runs sit under a
    ``TestRuns/`` parent. Anything else (e.g. the project's
    ``.exlab-wizard/`` cache) is skipped.
    """
    if not staging_root.exists():
        return []
    leaves: list[Path] = []
    for equipment_dir in iter_subdirs(staging_root):
        for project_dir in iter_subdirs(equipment_dir):
            for child in iter_subdirs(project_dir):
                if child.name == TEST_RUNS_DIR_NAME:
                    for run_dir in iter_subdirs(child):
                        if is_test_run_dir(run_dir.name):
                            leaves.append(run_dir)
                elif is_run_dir(child.name):
                    leaves.append(child)
    return leaves


def count_files_and_bytes(run_path: Path, *, exclude_cache: bool = True) -> tuple[int, int]:
    """Sum file count + total bytes under ``run_path``.

    With ``exclude_cache=True`` (default) the ``.exlab-wizard/`` subtree
    of the run is excluded so the byte total reflects only staged data.
    Used by both the panel summary (excludes cache) and the cleanup
    accounting (includes cache because the whole run is deleted).
    """
    files = 0
    total = 0
    stack: list[Path] = [run_path]
    while stack:
        current = stack.pop()
        try:
            iterator = list(os.scandir(current))
        except (FileNotFoundError, NotADirectoryError, PermissionError):
            continue
        for entry in iterator:
            if exclude_cache and entry.name == CACHE_DIR_NAME and current == run_path:
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    stack.append(Path(entry.path))
                    continue
                if entry.is_file(follow_symlinks=False):
                    files += 1
                    total += entry.stat(follow_symlinks=False).st_size
            except OSError:
                continue
    return files, total
