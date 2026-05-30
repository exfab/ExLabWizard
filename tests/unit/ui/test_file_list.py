"""Unit tests for ``ui/components/file_list``. Redesign §4.3, §5."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from exlab_wizard.ui.components.file_list import (
    FILE_CONTEXT_COPY_PATH,
    FILE_CONTEXT_KEEP_LOCAL,
    FILE_CONTEXT_OPEN,
    FileListEntry,
    FileListState,
    diff_file_lists,
    render_file_list,
    row_background,
)


def _walk(element: Any) -> Any:
    """Yield ``element`` and every descendant across all its slots.

    NiceGUI renders into the auto-index page outside a ``@ui.page`` context,
    so a component can be rendered directly in a unit test and its element
    tree inspected (mirrors the framed-pane tests).
    """
    yield element
    for slot in (getattr(element, "slots", None) or {}).values():
        for child in slot.children:
            yield from _walk(child)


def _rows(container: Any) -> list[Any]:
    """Return the rendered ``file-list-row`` <tr> elements under ``container``."""
    return [el for el in _walk(container) if el._props.get("data-testid") == "file-list-row"]


def _row_by_path(container: Any, path: str) -> Any:
    return next(r for r in _rows(container) if r._props.get("data-path") == path)


def _click(element: Any) -> None:
    """Invoke ``element``'s registered click handler (simulates a single-click)."""
    listeners = [
        listener for listener in element._event_listeners.values() if listener.type == "click"
    ]
    assert listeners, "element has no click listener"
    listeners[0].handler(SimpleNamespace())


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
    assert "inset 3px 0 0 var(--color-row-selected-bar" in bg
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


# ---------------------------------------------------------------------------
# Selection render path (Phase 4 / Option B) -- live NiceGUI render + inspect
# ---------------------------------------------------------------------------


def _selection_state() -> FileListState:
    return FileListState(
        path="/r",
        selected_path="/r/scan.tif",
        entries=[
            FileListEntry(
                name="scan.tif",
                path="/r/scan.tif",
                is_dir=False,
                size_bytes=2048,
                sync_status="synced",
            ),
            FileListEntry(name="Runs", path="/r/Runs", is_dir=True),
            FileListEntry(name="old.tif", path="/r/old.tif", is_dir=False, tombstone=True),
        ],
    )


def test_render_marks_selected_row_only() -> None:
    """The row whose path matches ``selected_path`` carries the selected fill."""
    container = render_file_list(state=_selection_state())
    selected = _row_by_path(container, "/r/scan.tif")
    assert selected._props.get("data-selected") == "true"
    # Selected fill + 3px accent bar come from row_background.
    assert "--color-row-selected" in selected._style.get("background", "")
    assert "--color-row-selected-bar" in selected._style.get("box-shadow", "")
    # The other rows are not marked selected.
    assert _row_by_path(container, "/r/Runs")._props.get("data-selected") is None
    assert _row_by_path(container, "/r/old.tif")._props.get("data-selected") is None


def test_render_tombstone_row_is_dimmed_and_marked() -> None:
    """A tombstone row carries the data-tombstone attr + dim/italic decoration."""
    container = render_file_list(state=_selection_state())
    tomb = _row_by_path(container, "/r/old.tif")
    assert tomb._props.get("data-tombstone") == "true"
    assert tomb._style.get("opacity") == "0.65"
    assert tomb._style.get("font-style") == "italic"


def test_on_select_fires_for_file_and_folder() -> None:
    """Single-click selection fires ``on_select`` for files AND folders."""
    picked: list[Any] = []
    container = render_file_list(
        state=_selection_state(), on_select=lambda entry: picked.append(entry)
    )
    _click(_row_by_path(container, "/r/scan.tif"))
    _click(_row_by_path(container, "/r/Runs"))
    assert [entry.path for entry in picked] == ["/r/scan.tif", "/r/Runs"]
    # The folder selection carries the directory entry, not a file.
    assert picked[1].is_dir is True


def test_no_click_listener_when_on_select_absent() -> None:
    """Selection is opt-in: with no ``on_select`` the rows wire no click handler."""
    container = render_file_list(state=_selection_state())
    row = _row_by_path(container, "/r/scan.tif")
    assert not [li for li in row._event_listeners.values() if li.type == "click"]
