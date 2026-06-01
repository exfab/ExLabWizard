"""Tests for symlink-safe cleanup candidate planning and deletion."""

from __future__ import annotations

from pathlib import Path

from exlab_wizard.constants import CACHE_DIR_NAME
from exlab_wizard.sync.run_delete import collect_cleanup_candidates, delete_run_files


def test_collect_cleanup_candidates_excludes_cache_keep_local_symlink_dirs_and_ignored(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "Run_x"
    run_dir.mkdir()
    (run_dir / "drop.bin").write_bytes(b"drop")
    (run_dir / "keep.bin").write_bytes(b"keep")
    (run_dir / "scan.tmp").write_bytes(b"ignored")
    cache = run_dir / CACHE_DIR_NAME
    cache.mkdir()
    (cache / "creation.json").write_text("{}")
    external = tmp_path / "external"
    external.mkdir()
    (external / "outside.txt").write_text("outside")
    (run_dir / "linked_dir").symlink_to(external, target_is_directory=True)

    candidates = collect_cleanup_candidates(
        run_dir,
        keep_local={"keep.bin"},
        ignore_globs=["*.tmp"],
        delete_ignored=False,
    )

    assert candidates.delete == ("drop.bin",)
    assert candidates.retained_ignored == ("scan.tmp",)


def test_collect_cleanup_candidates_includes_ignored_when_delete_ignored_true(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "Run_x"
    run_dir.mkdir()
    (run_dir / "scan.tmp").write_bytes(b"ignored")

    candidates = collect_cleanup_candidates(
        run_dir,
        keep_local=set(),
        ignore_globs=["*.tmp"],
        delete_ignored=True,
    )

    assert candidates.delete == ("scan.tmp",)
    assert candidates.retained_ignored == ()


def test_delete_run_files_delete_only_leaves_unlisted_late_file(tmp_path: Path) -> None:
    run_dir = tmp_path / "Run_x"
    run_dir.mkdir()
    (run_dir / "verified.bin").write_bytes(b"verified")
    (run_dir / "late.bin").write_bytes(b"late")

    delete_run_files(
        run_dir,
        keep_local=set(),
        retain_cache=True,
        delete_only={"verified.bin"},
    )

    assert not (run_dir / "verified.bin").exists()
    assert (run_dir / "late.bin").exists()
    assert run_dir.exists()


def test_delete_run_files_delete_only_retain_cache_false_removes_empty_root(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "Run_x"
    run_dir.mkdir()
    cache = run_dir / CACHE_DIR_NAME
    cache.mkdir()
    (cache / "creation.json").write_text("{}")

    delete_run_files(
        run_dir,
        keep_local=set(),
        retain_cache=False,
        delete_only=set(),
    )

    assert not run_dir.exists()


def test_delete_run_files_delete_only_retain_cache_false_keeps_root_with_late_file(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "Run_x"
    run_dir.mkdir()
    (run_dir / "late.bin").write_bytes(b"late")
    cache = run_dir / CACHE_DIR_NAME
    cache.mkdir()
    (cache / "creation.json").write_text("{}")

    delete_run_files(
        run_dir,
        keep_local=set(),
        retain_cache=False,
        delete_only=set(),
    )

    assert run_dir.exists()
    assert (run_dir / "late.bin").exists()
    assert not cache.exists()
