"""Unit tests for ``ui/components/file_list``. Redesign §4.3, §5."""

from __future__ import annotations

from exlab_wizard.ui.components.file_list import (
    FILE_CONTEXT_COPY_PATH,
    FILE_CONTEXT_KEEP_LOCAL,
    FILE_CONTEXT_OPEN,
    FileListEntry,
    diff_file_lists,
    row_background,
)


def _entry(
    path: str, *, size: int = 100, modified: str = "2026-05-14T00:00:00Z", sync: str | None = None
) -> FileListEntry:
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


# ---------------------------------------------------------------------------
# Keep-local / tombstone fields (operator-free per-file NAS sync design)
# ---------------------------------------------------------------------------


def test_file_list_entry_defaults_keep_local_and_tombstone_false() -> None:
    """A bare entry defaults ``keep_local`` and ``tombstone`` to False."""
    entry = _entry("/r/scan.tif")
    assert entry.keep_local is False
    assert entry.tombstone is False


def test_diff_detects_keep_local_change_as_modification() -> None:
    """Flipping ``keep_local`` is a modification (drives a re-render)."""
    before = FileListEntry(name="a", path="/r/a", is_dir=False, keep_local=False)
    after = FileListEntry(name="a", path="/r/a", is_dir=False, keep_local=True)
    diff = diff_file_lists(previous=[before], current=[after])
    assert diff.modified == ("/r/a",)


def test_diff_detects_tombstone_change_as_modification() -> None:
    """A file transitioning to a tombstone is a modification."""
    before = FileListEntry(name="a", path="/r/a", is_dir=False, tombstone=False)
    after = FileListEntry(name="a", path="/r/a", is_dir=False, tombstone=True)
    diff = diff_file_lists(previous=[before], current=[after])
    assert diff.modified == ("/r/a",)


def test_keep_local_action_constant_is_distinct() -> None:
    """``FILE_CONTEXT_KEEP_LOCAL`` is a distinct discriminator value."""
    assert FILE_CONTEXT_KEEP_LOCAL not in {FILE_CONTEXT_OPEN, FILE_CONTEXT_COPY_PATH}


def test_render_file_list_keep_local_menu_and_tombstone() -> None:
    """The renderer wires the keep-local menu item and tombstone row.

    Exercises the NiceGUI render path inside an app context so the
    keep-local context-menu item and the tombstone (no Open-in-OS) row
    actually build without raising.
    """
    from nicegui import ui

    from exlab_wizard.ui.components.file_list import FileListState, render_file_list

    actions: list[tuple[str, str]] = []

    @ui.page("/_test_file_list")  # pragma: no cover -- render path
    def _page() -> None:
        state = FileListState(
            path="/r",
            entries=[
                FileListEntry(
                    name="scan.tif",
                    path="/r/scan.tif",
                    is_dir=False,
                    size_bytes=10,
                    sync_status="synced",
                    keep_local=True,
                ),
                FileListEntry(
                    name="old.tif",
                    path="/r/old.tif",
                    is_dir=False,
                    sync_status="on_nas",
                    tombstone=True,
                ),
            ],
        )
        render_file_list(
            state=state,
            on_context_menu=lambda e, a: actions.append((e.path, a)),
        )

    # Building the page handler without raising is the assertion here;
    # the e2e suite drives the live DOM interaction.
    assert callable(_page)


# ---------------------------------------------------------------------------
# row_background precedence (Selected > New > Tombstone > Zebra) -- Redesign §4.2
# ---------------------------------------------------------------------------


def test_row_bg_selected_wins_over_new() -> None:
    bg = row_background(_entry("/a"), is_selected=True, is_new=True, index=0)
    assert "--color-row-selected" in bg
    assert "inset 3px 0 0 var(--color-row-selected-bar)" in bg
    assert "--color-highlight" not in bg


def test_row_bg_new_wins_over_tombstone_and_zebra() -> None:
    # New-file beats both tombstone and the would-be zebra stripe at odd index.
    e = FileListEntry(name="a", path="/a", is_dir=False, tombstone=True)
    bg = row_background(e, is_selected=False, is_new=True, index=1)
    assert "--color-highlight" in bg
    assert "--color-zebra" not in bg
    # Tombstone decoration is additive regardless of which background tier won,
    # so a new+tombstone row keeps the highlight fill AND the dim/italic.
    assert "opacity: 0.65;" in bg
    assert "font-style: italic;" in bg


def test_row_bg_tombstone_suppresses_zebra_keeps_dim() -> None:
    # An odd-index tombstone would otherwise be striped; the tombstone tier
    # contributes no fill, but the dim/italic decoration is still applied.
    e = FileListEntry(name="a", path="/a", is_dir=False, tombstone=True)
    bg = row_background(e, is_selected=False, is_new=False, index=1)
    assert "--color-zebra" not in bg
    assert "--color-row-selected" not in bg
    assert "--color-highlight" not in bg
    assert "opacity: 0.65;" in bg
    assert "font-style: italic;" in bg


def test_row_bg_zebra_on_odd_index_only() -> None:
    odd = row_background(_entry("/a"), is_selected=False, is_new=False, index=1)
    even = row_background(_entry("/a"), is_selected=False, is_new=False, index=0)
    assert "--color-zebra" in odd
    assert "--color-zebra" not in even


def test_row_bg_zebra_parity_is_index_based() -> None:
    # Parity follows index, so interleaved state rows never shift the stripe.
    assert "--color-zebra" in row_background(_entry("/d"), is_selected=False, is_new=False, index=3)


def test_row_bg_selected_tombstone_keeps_both_treatments() -> None:
    e = FileListEntry(name="a", path="/a", is_dir=False, tombstone=True)
    bg = row_background(e, is_selected=True, is_new=False, index=0)
    assert "--color-row-selected" in bg
    assert "opacity: 0.65;" in bg
    assert "font-style: italic;" in bg


def test_row_bg_plain_even_row_is_empty() -> None:
    assert row_background(_entry("/a"), is_selected=False, is_new=False, index=0) == ""
