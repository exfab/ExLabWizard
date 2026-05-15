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

# Node-kind discriminators consumed by the dispatcher.
NODE_KIND_EQUIPMENT = "equipment"
NODE_KIND_RECEIVED_EQUIPMENT = "received_equipment"
NODE_KIND_PROJECT = "project"
NODE_KIND_RUNS_FOLDER = "runs_folder"
NODE_KIND_TEST_RUNS_FOLDER = "test_runs_folder"
NODE_KIND_RUN = "run"
NODE_KIND_RECEIVED_RUN = "received_run"


@dataclass(frozen=True)
class MetadataPaneState:
    """Mutable state for the metadata pane."""

    selected_node: str | None = None
    node_kind: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    """Node-kind-specific data: equipment dict, run dict, lifecycle dict, etc."""


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
        if state.selected_node is None or state.node_kind is None:
            ui.label("Select a node to see its metadata.").props(
                'data-testid="metadata-pane-empty"'
            ).style("color: var(--color-muted);")
            return container
        if state.node_kind == NODE_KIND_EQUIPMENT:
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
    return container


def _kv(key: str, value: Any) -> None:  # pragma: no cover -- NiceGUI render, driven by e2e
    try:
        from nicegui import ui
    except Exception:
        return
    with ui.row().classes("items-center w-full"):
        ui.label(f"{key}:").style("color: var(--color-muted); width: 12rem; min-width: 12rem;")
        ui.label(str(value) if value is not None else "-").style("font-family: var(--font-mono);")


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
    _kv("Completeness signal", payload.get("completeness_signal"))
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
