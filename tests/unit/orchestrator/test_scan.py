"""Unit tests for ``exlab_wizard.orchestrator._scan``.

The shared filesystem helpers used by both ``staging_query`` and
``staging_watcher``. Covers the §13.2 walk pattern and the
file-count / byte-total accounting (with and without the cache dir).
"""

from __future__ import annotations

from pathlib import Path

from exlab_wizard.orchestrator._scan import (
    count_files_and_bytes,
    iter_subdirs,
    walk_run_leaves,
)

# ---------------------------------------------------------------------------
# iter_subdirs
# ---------------------------------------------------------------------------


def test_iter_subdirs_returns_empty_for_missing_path(tmp_path: Path) -> None:
    assert iter_subdirs(tmp_path / "missing") == []


def test_iter_subdirs_skips_cache_dir_and_dotfiles(tmp_path: Path) -> None:
    (tmp_path / "EQ1").mkdir()
    (tmp_path / "EQ2").mkdir()
    (tmp_path / ".exlab-wizard").mkdir()
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "file.txt").write_text("x")

    out = sorted(p.name for p in iter_subdirs(tmp_path))
    assert out == ["EQ1", "EQ2"]


def test_iter_subdirs_does_not_follow_symlinks(tmp_path: Path) -> None:
    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target)
    out = sorted(p.name for p in iter_subdirs(tmp_path))
    # The symlink resolves to a directory but we explicitly don't follow.
    assert "real" in out
    assert "link" not in out


# ---------------------------------------------------------------------------
# walk_run_leaves
# ---------------------------------------------------------------------------


def test_walk_run_leaves_returns_empty_when_root_missing(tmp_path: Path) -> None:
    assert walk_run_leaves(tmp_path / "missing") == []


def test_walk_run_leaves_finds_experimental_and_test_runs(tmp_path: Path) -> None:
    (tmp_path / "EQ1" / "PROJ-0001" / "Run_2026-04-17T14-32-00").mkdir(parents=True)
    (tmp_path / "EQ1" / "PROJ-0001" / "TestRuns" / "TestRun_2026-04-17T09-12-00").mkdir(parents=True)
    # A directory NOT matching either prefix should be ignored.
    (tmp_path / "EQ1" / "PROJ-0001" / "_drafts").mkdir(parents=True)
    leaves = sorted(p.name for p in walk_run_leaves(tmp_path))
    assert leaves == ["Run_2026-04-17T14-32-00", "TestRun_2026-04-17T09-12-00"]


def test_walk_run_leaves_handles_empty_tree(tmp_path: Path) -> None:
    """An empty staging_root has no run leaves -- not an error."""
    assert walk_run_leaves(tmp_path) == []


# ---------------------------------------------------------------------------
# count_files_and_bytes
# ---------------------------------------------------------------------------


def test_count_files_and_bytes_excludes_cache_dir_by_default(tmp_path: Path) -> None:
    (tmp_path / "data.bin").write_bytes(b"a" * 100)
    (tmp_path / ".exlab-wizard").mkdir()
    (tmp_path / ".exlab-wizard" / "ingest.json").write_bytes(b"x" * 50)
    files, total = count_files_and_bytes(tmp_path, exclude_cache=True)
    assert files == 1
    assert total == 100


def test_count_files_and_bytes_includes_cache_dir_when_requested(tmp_path: Path) -> None:
    (tmp_path / "data.bin").write_bytes(b"a" * 100)
    (tmp_path / ".exlab-wizard").mkdir()
    (tmp_path / ".exlab-wizard" / "ingest.json").write_bytes(b"x" * 50)
    files, total = count_files_and_bytes(tmp_path, exclude_cache=False)
    assert files == 2
    assert total == 150


def test_count_files_and_bytes_returns_zero_for_missing_path(tmp_path: Path) -> None:
    files, total = count_files_and_bytes(tmp_path / "missing")
    assert files == 0
    assert total == 0


def test_count_files_and_bytes_descends_into_subdirectories(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "b").mkdir()
    (tmp_path / "a" / "b" / "c.bin").write_bytes(b"x" * 30)
    files, total = count_files_and_bytes(tmp_path)
    assert files == 1
    assert total == 30
