"""Unit tests for ``ui/components/file_list``. Redesign §4.3, §5."""

from __future__ import annotations

from exlab_wizard.ui.components.file_list import (
    FileListEntry,
    diff_file_lists,
)


def _entry(path: str, *, size: int = 100, modified: str = "2026-05-14T00:00:00Z", sync: str | None = None) -> FileListEntry:
    return FileListEntry(
        name=path.rsplit("/", 1)[-1],
        path=path,
        is_dir=False,
        size_bytes=size,
        modified_iso=modified,
        sync_status=sync,
    )


def test_diff_detects_additions() -> None:
    diff = diff_file_lists(
        previous=[],
        current=[_entry("/r/scan.tif"), _entry("/r/meta.json")],
    )
    assert diff.added == ("/r/meta.json", "/r/scan.tif")
    assert diff.removed == ()
    assert diff.modified == ()


def test_diff_detects_removals() -> None:
    diff = diff_file_lists(
        previous=[_entry("/r/scan.tif"), _entry("/r/meta.json")],
        current=[_entry("/r/scan.tif")],
    )
    assert diff.added == ()
    assert diff.removed == ("/r/meta.json",)
    assert diff.modified == ()


def test_diff_detects_size_change_as_modification() -> None:
    diff = diff_file_lists(
        previous=[_entry("/r/scan.tif", size=100)],
        current=[_entry("/r/scan.tif", size=200)],
    )
    assert diff.modified == ("/r/scan.tif",)
    assert diff.added == ()
    assert diff.removed == ()


def test_diff_detects_sync_status_change_as_modification() -> None:
    diff = diff_file_lists(
        previous=[_entry("/r/scan.tif", sync="pending")],
        current=[_entry("/r/scan.tif", sync="synced")],
    )
    assert diff.modified == ("/r/scan.tif",)


def test_diff_unchanged_is_empty() -> None:
    entries = [_entry("/r/scan.tif"), _entry("/r/meta.json")]
    diff = diff_file_lists(previous=entries, current=entries)
    assert diff.added == ()
    assert diff.removed == ()
    assert diff.modified == ()
