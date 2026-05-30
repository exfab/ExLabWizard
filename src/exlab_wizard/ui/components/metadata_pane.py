"""Node-type-aware metadata pane for the rebuilt main window.

GUI/Orchestrator Redesign §4.4. Renders the right-pane Metadata tab
content based on the kind of tree node selected.

Pure render function: takes the selected node id + a payload dict shaped
by the caller (typically derived from GET /tree + GET /run responses)
and dispatches to the per-kind renderer. The Problems tab is rendered
separately by the existing problems-row machinery.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from exlab_wizard.ui.components.empty_state import empty_state
from exlab_wizard.ui.components.file_type_icon import file_type_icon
from exlab_wizard.ui.components.sync_status_icon import STATUS_ON_NAS, sync_status_icon

# Node-kind discriminators consumed by the dispatcher.
NODE_KIND_EQUIPMENT = "equipment"
NODE_KIND_RECEIVED_EQUIPMENT = "received_equipment"
NODE_KIND_PROJECT = "project"
NODE_KIND_RUNS_FOLDER = "runs_folder"
NODE_KIND_TEST_RUNS_FOLDER = "test_runs_folder"
NODE_KIND_RUN = "run"
NODE_KIND_RECEIVED_RUN = "received_run"

# Selected-file/folder sub-card kinds (Phase 4 / Option B), set by the mount
# layer on the payload it assembles (see mount._build_selected_file).
SELECTED_KIND_FILE = "file"
SELECTED_KIND_FOLDER = "folder"


@dataclass(frozen=True)
class MetadataPaneState:
    """Mutable state for the metadata pane."""

    selected_node: str | None = None
    node_kind: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    """Node-kind-specific data: equipment dict, run dict, lifecycle dict, etc."""
    selected_file: dict[str, Any] | None = None
    """Phase 4 / Option B (spec §4.4): a file/folder selected in the centre
    list, assembled by mount._build_selected_file. Rendered as a sub-card
    *beneath* the node-kind content (and beneath the empty state), so the
    selection's metadata shows whether or not a tree node is also selected."""


def selected_file_card_title(payload: dict[str, Any]) -> str:
    """Return the sub-card heading for a selected file/folder payload (pure).

    ``"Selected folder"`` for a folder payload (``kind == "folder"``),
    ``"Selected file"`` otherwise. Pure so the title mapping is testable
    without spinning up NiceGUI.
    """
    if payload.get("kind") == SELECTED_KIND_FOLDER:
        return "Selected folder"
    return "Selected file"


def render_metadata_pane(
    *,
    state: MetadataPaneState,
    on_run_staging_action: Callable[[str, str], None] | None = None,
) -> Any:  # pragma: no cover -- NiceGUI render, driven by e2e
    """Render the right-pane Metadata content for ``state.selected_node``.

    Dispatches to one of the per-kind renderers. ``on_run_staging_action``
    is the callback invoked by the run-level staging actions (force sync /
    clear / view log) per Redesign §4.6.
    """
    try:
        from nicegui import ui
    except Exception:
        return {"state": state}

    with (
        ui.column()
        .classes("w-full p-4")
        .style("gap: 0.5rem;")
        .props('data-testid="metadata-pane"') as container
    ):
        # The node-kind dispatch and the selected-file sub-card are an
        # if/elif chain that *falls through* (no early return): a file/folder
        # selected in the centre list appends its own sub-card BELOW whatever
        # the node dispatch rendered -- including the empty state -- so the
        # selection's metadata is visible whether or not a tree node is also
        # selected (Phase 4 / Option B, spec §4.4).
        if state.selected_node is None or state.node_kind is None:
            empty_state(
                icon="info",
                message="Select a node to see its metadata.",
                testid="metadata-pane-empty",
            )
        elif state.node_kind == NODE_KIND_EQUIPMENT:
            _render_equipment(state.payload)
        elif state.node_kind == NODE_KIND_RECEIVED_EQUIPMENT:
            _render_received_equipment(state.payload)
        elif state.node_kind == NODE_KIND_PROJECT:
            _render_project(state.payload)
        elif state.node_kind in (NODE_KIND_RUNS_FOLDER, NODE_KIND_TEST_RUNS_FOLDER):
            _render_runs_folder(state.node_kind, state.payload)
        elif state.node_kind == NODE_KIND_RUN:
            _render_run(state.payload, on_run_staging_action=on_run_staging_action)
        elif state.node_kind == NODE_KIND_RECEIVED_RUN:
            _render_received_run(state.payload)
        else:
            ui.label(f"Unknown node kind: {state.node_kind}").style("color: var(--color-muted);")
        if state.selected_file:
            _render_selected_file_card(state.selected_file)
    return container


def _kv(key: str, value: Any) -> None:  # pragma: no cover -- NiceGUI render, driven by e2e
    try:
        from nicegui import ui
    except Exception:
        return
    with ui.row().classes("items-center").style("flex-wrap: nowrap;"):
        ui.label(f"{key}:").style(
            "color: var(--color-muted); width: 12rem; min-width: 12rem; white-space: nowrap;"
        )
        # Values overflow rather than wrap (long paths stay on one line); the
        # metadata pane is horizontally scrollable so they remain reachable.
        ui.label(str(value) if value is not None else "-").style(
            "font-family: var(--font-mono); white-space: nowrap;"
        )


def _kv_sync(key: str, status: Any) -> None:  # pragma: no cover -- NiceGUI render, driven by e2e
    """Key/value row whose value is a sync-status icon (tolerant).

    Mirrors :func:`_kv`'s label column but renders the status through the
    tolerant icon path (``strict=False``): a ``None`` / unknown status shows
    a neutral dash rather than raising (spec §4.5).
    """
    try:
        from nicegui import ui
    except Exception:
        return
    with ui.row().classes("items-center w-full"):
        ui.label(f"{key}:").style("color: var(--color-muted); width: 12rem; min-width: 12rem;")
        sync_status_icon(status, strict=False)


def _render_selected_file_card(
    payload: dict[str, Any],
) -> None:  # pragma: no cover -- NiceGUI render, driven by e2e
    """Render the SELECTED FILE / SELECTED FOLDER sub-card (Phase 4, §4.4).

    Appended beneath the node-kind content. A file shows
    name/size/modified/sync/path; a tombstone ("On NAS") omits the size row
    and notes that the local copy is gone. A folder shows
    name/item-count/sync-rollup/path with no size row -- the recursive total
    is deferred (spec §11).
    """
    try:
        from nicegui import ui
    except Exception:
        return
    is_folder = payload.get("kind") == SELECTED_KIND_FOLDER
    testid = "metadata-selected-folder" if is_folder else "metadata-selected-file"
    with (
        ui.column()
        .classes("w-full")
        .style(
            "gap: 0.4rem; margin-top: 0.75rem; padding-top: 0.75rem; "
            "border-top: 1px solid var(--color-rule);"
        )
        .props(f'data-testid="{testid}"')
    ):
        ui.label(selected_file_card_title(payload).upper()).style(
            "font-size: var(--text-xs); text-transform: uppercase; letter-spacing: 0.08em; "
            "color: var(--color-muted); font-weight: 600;"
        )
        with ui.row().classes("items-center").style("gap: 0.4rem;"):
            file_type_icon(payload.get("name", ""), is_dir=is_folder)
            ui.label(payload.get("name", "")).style(
                "font-family: var(--font-display); color: var(--color-heading); font-weight: 600;"
            )
        if is_folder:
            _kv("Items", payload.get("item_count"))
            _kv_sync("Sync", payload.get("rollup"))
            _kv("Path", payload.get("path"))
        else:
            tombstone = bool(payload.get("tombstone"))
            if not tombstone:
                _kv("Size", payload.get("size"))
            _kv("Modified", payload.get("modified"))
            _kv_sync(
                "Sync",
                payload.get("sync_status") or (STATUS_ON_NAS if tombstone else None),
            )
            _kv("Path", payload.get("path"))
            if tombstone:
                ui.label("On NAS -- local copy cleared.").props(
                    'data-testid="metadata-selected-file-tombstone-note"'
                ).style("color: var(--color-muted); margin-top: 0.25rem;")


def _render_equipment(
    payload: dict[str, Any],
) -> None:  # pragma: no cover -- NiceGUI render, driven by e2e
    try:
        from nicegui import ui
    except Exception:
        return
    ui.label(payload.get("label", payload.get("id", ""))).style(
        "font-family: var(--font-display); font-size: var(--text-md); "
        "color: var(--color-heading); font-weight: 600;"
    )
    _kv("ID", payload.get("id"))
    _kv("Label", payload.get("label"))
    _kv("Sync mode", payload.get("sync_mode"))
    _kv("Local root", payload.get("local_root"))
    _kv("NAS root", payload.get("nas_root"))
    if payload.get("sync_mode") == "stage":
        ui.label(
            "Stage mode: this device pushes runs to a connected PC's staging "
            "area. Per-run sync status tops out at 'relayed' locally; the "
            "connected PC owns the onward NAS sync."
        ).style("color: var(--color-muted); margin-top: 0.5rem;").props(
            'data-testid="metadata-stage-ceiling-note"'
        )


def _render_received_equipment(
    payload: dict[str, Any],
) -> None:  # pragma: no cover -- NiceGUI render, driven by e2e
    try:
        from nicegui import ui
    except Exception:
        return
    ui.label(payload.get("label", payload.get("id", ""))).style(
        "font-family: var(--font-display); font-size: var(--text-md); "
        "color: var(--color-heading); font-weight: 600;"
    )
    ui.element("span").props('data-testid="metadata-relay-badge"').style(
        "background: var(--color-info); color: var(--color-on-info); "
        "padding: 2px 8px; border-radius: 4px; font-size: var(--text-xs);"
    )
    _kv("Relay source", payload.get("source_host"))
    _kv("Equipment ID", payload.get("id"))
    _kv("Equipment label", payload.get("label"))


def _render_project(
    payload: dict[str, Any],
) -> None:  # pragma: no cover -- NiceGUI render, driven by e2e
    _kv("Name", payload.get("name"))
    _kv("LIMS short id", payload.get("short_id"))
    _kv("Objective", payload.get("objective"))
    _kv("Run count", payload.get("run_count"))
    _kv("Test run count", payload.get("test_run_count"))


def _render_runs_folder(
    kind: str, payload: dict[str, Any]
) -> None:  # pragma: no cover -- NiceGUI render, driven by e2e
    try:
        from nicegui import ui
    except Exception:
        return
    label = "Runs/" if kind == NODE_KIND_RUNS_FOLDER else "TestRuns/"
    ui.label(f"{label} (group)").style(
        "font-family: var(--font-display); color: var(--color-heading);"
    )
    _kv("Path", payload.get("path"))
    _kv("Child run count", payload.get("run_count"))


def _render_run(
    payload: dict[str, Any],
    *,
    on_run_staging_action: Callable[[str, str], None] | None,
) -> None:  # pragma: no cover -- NiceGUI render, driven by e2e
    try:
        from nicegui import ui
    except Exception:
        return
    ui.label(payload.get("label", payload.get("name", ""))).style(
        "font-family: var(--font-display); font-size: var(--text-md); "
        "color: var(--color-heading); font-weight: 600;"
    )
    _kv("Kind", payload.get("run_kind"))
    _kv("Operator", payload.get("operator"))
    _kv("Objective", payload.get("objective"))
    _kv("Template", payload.get("template"))
    _kv("Created", payload.get("created_at"))
    _kv("LIMS project", payload.get("lims_project"))
    _kv("Sync status", payload.get("sync_status"))
    if on_run_staging_action is not None:
        with ui.row().classes("items-center").style("gap: 0.5rem; margin-top: 0.5rem;"):
            run_path = payload.get("path", "")
            ui.button("Force sync").props('flat data-testid="metadata-run-force-sync"').on(
                "click",
                lambda _evt: on_run_staging_action(run_path, "force_sync"),
            )
            ui.button("Clear verified").props('flat data-testid="metadata-run-clear-verified"').on(
                "click",
                lambda _evt: on_run_staging_action(run_path, "clear_verified"),
            )
            ui.button("View log").props('flat data-testid="metadata-run-view-log"').on(
                "click",
                lambda _evt: on_run_staging_action(run_path, "view_log"),
            )


def _render_received_run(
    payload: dict[str, Any],
) -> None:  # pragma: no cover -- NiceGUI render, driven by e2e
    _kv("Run path", payload.get("path"))
    _kv("Lifecycle state", payload.get("ingest_state"))
    _kv("Files received", payload.get("files_received"))
    _kv("Bytes received", payload.get("bytes_received"))
    _kv("Last activity", payload.get("last_activity_at"))
