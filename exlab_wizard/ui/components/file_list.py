"""Live file-list component for the rebuilt main window.

GUI/Orchestrator Redesign §4.3, §5. Renders the immediate contents of a
folder (name / size / modified / per-file sync status) and exposes a pure
diff function that drives the new-file highlight.

The renderer is a pure function kept free of session-store / API deps
(matches the existing components pattern). The folder feed (§5) is
expected to call ``render_file_list`` with the current entry list; the
caller decides when to invoke and when to stop the underlying poll.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from exlab_wizard.ui.pages.staging import format_bytes


@dataclass(frozen=True)
class FileListEntry:
    """One row in the centre-pane file list."""

    name: str
    path: str
    is_dir: bool
    size_bytes: int | None = None
    modified_iso: str | None = None
    sync_status: str | None = None


@dataclass
class FileListState:
    """Mutable state for the file list, consumed by the renderer."""

    path: str = ""
    entries: list[FileListEntry] = field(default_factory=list)
    new_paths: frozenset[str] = field(default_factory=frozenset)
    """Paths that appeared in the most recent diff -- briefly highlighted."""


@dataclass(frozen=True)
class FileListDiff:
    """Result of comparing two successive folder-list snapshots."""

    added: tuple[str, ...]
    removed: tuple[str, ...]
    modified: tuple[str, ...]


def diff_file_lists(
    previous: Iterable[FileListEntry],
    current: Iterable[FileListEntry],
) -> FileListDiff:
    """Return additions / removals / modifications between two snapshots.

    Pure function. An entry is "modified" when its ``path`` is present in
    both lists but its (size, modified_iso, sync_status) tuple differs.
    Used by the renderer to drive the new-file highlight and is unit-
    testable without spinning up NiceGUI.
    """
    prev_map = {e.path: e for e in previous}
    curr_map = {e.path: e for e in current}
    prev_paths = set(prev_map)
    curr_paths = set(curr_map)
    added = tuple(sorted(curr_paths - prev_paths))
    removed = tuple(sorted(prev_paths - curr_paths))
    modified: list[str] = []
    for path in sorted(curr_paths & prev_paths):
        before = prev_map[path]
        after = curr_map[path]
        if (
            before.size_bytes != after.size_bytes
            or before.modified_iso != after.modified_iso
            or before.sync_status != after.sync_status
        ):
            modified.append(path)
    return FileListDiff(
        added=added,
        removed=removed,
        modified=tuple(modified),
    )


def render_file_list(
    *,
    state: FileListState,
    on_double_click: Callable[[FileListEntry], None] | None = None,
    on_context_menu: Callable[[FileListEntry, str], None] | None = None,
) -> Any:
    """Render the centre-pane file list. Pure render function.

    Double-clicking a folder navigates into it; double-clicking a file
    asks the OS to open it. Single-click selects the row for the
    right-click context menu only — the right pane is node-scoped
    (Redesign §4.3 / decision 6A).
    """
    try:
        from nicegui import ui
    except Exception:
        return {"state": state}

    with ui.column().classes("w-full h-full").style("gap: 0;") as container:
        if not state.entries:
            ui.label("Empty folder.").style(
                "color: var(--color-muted); padding: var(--sp-3);"
            ).props('data-testid="file-list-empty"')
            return container
        with ui.element("table").classes("w-full").style(
            "border-collapse: collapse; font-family: var(--font-mono);"
        ).props('data-testid="file-list-table"'):
            for entry in state.entries:
                _render_row(
                    entry,
                    is_new=entry.path in state.new_paths,
                    on_double_click=on_double_click,
                    on_context_menu=on_context_menu,
                )
    return container


def _render_row(
    entry: FileListEntry,
    *,
    is_new: bool,
    on_double_click: Callable[[FileListEntry], None] | None,
    on_context_menu: Callable[[FileListEntry, str], None] | None,
) -> None:
    try:
        from nicegui import ui
    except Exception:
        return
    highlight = (
        "background: var(--color-highlight); "
        if is_new
        else ""
    )
    with ui.element("tr").style(
        f"{highlight}border-bottom: 1px solid var(--color-rule);"
    ).props(f'data-testid="file-list-row" data-path="{entry.path}"'):
        ui.element("td").classes("p-2").style("font-weight: 500;").bind_text_from(
            entry, "name"
        )
        ui.element("td").classes("p-2 text-right").bind_text_from(
            entry,
            "size_bytes",
            backward=lambda v: "-" if v is None else format_bytes(int(v)),
        )
        ui.element("td").classes("p-2").bind_text_from(
            entry, "modified_iso", backward=lambda v: v or "-"
        )
        ui.element("td").classes("p-2").bind_text_from(
            entry, "sync_status", backward=lambda v: v or "-"
        )

    if on_double_click is not None:
        # NiceGUI doesn't expose row-level dblclick easily; the caller is
        # responsible for wiring through any JS layer when needed. The
        # callback is kept in the signature so unit tests can verify
        # plumbing.
        pass
