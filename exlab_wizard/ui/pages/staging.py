"""Orchestrator staging panel (Frontend Spec §3.9, Backend §13.8).

A bottom-dock panel rendered below the main content when orchestrator
mode is enabled. The panel is ~120 px tall, non-collapsible, and always
visible while the orchestrator is active so the operator sees pending
staging activity without an extra navigation step.

Each row shows:

| State | Run | Equipment | Files | Bytes | Elapsed | Actions |

Per-row actions:

* ``[Force sync]`` -- POST /staging/{run_path}/force-sync.
* ``[Clear]`` -- POST /staging/{run_path}/clear (only enabled for
  ``sync_verified`` runs).
* ``[View log]`` -- open the run's wizard.<hostname>.log in the detail
  pane.

A toolbar action ``[Clear verified runs]`` clears every run currently
in ``sync_verified``. The row data is supplied by the caller as a list
of :class:`StagedRunSummary` from
:func:`exlab_wizard.orchestrator.staging_query.list_staged_runs`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from exlab_wizard.constants import IngestState
from exlab_wizard.logging import get_logger
from exlab_wizard.orchestrator.staging_query import StagedRunSummary

__all__ = [
    "STAGING_DOCK_HEIGHT_PX",
    "STAGING_TABLE_COLUMNS",
    "StagingDockState",
    "format_bytes",
    "format_elapsed",
    "render_staging_dock",
    "row_props",
    "state_pill_props",
]

_log = get_logger(__name__)


# Spec-derived constants -----------------------------------------------------

STAGING_DOCK_HEIGHT_PX: int = 120
"""Per the brief: ~120 px, non-collapsible bottom dock."""

STAGING_TABLE_COLUMNS: tuple[str, ...] = (
    "State",
    "Run",
    "Equipment",
    "Files",
    "Bytes",
    "Elapsed",
    "Actions",
)
"""The seven columns displayed (column order is part of the spec)."""


# State -> color mapping mirroring the design tokens in
# ``exlab_wizard.ui.design``. Kept here as plain strings so the unit tests
# don't depend on the full design module being importable.
_STATE_COLORS: dict[str, str] = {
    IngestState.STAGING.value: "var(--color-info)",
    IngestState.COMPLETE.value: "var(--color-success)",
    IngestState.SYNC_QUEUED.value: "var(--color-info)",
    IngestState.SYNC_VERIFIED.value: "var(--color-success)",
    IngestState.CLEARED.value: "var(--color-muted)",
}


@dataclass
class StagingDockState:
    """Render state for the staging panel.

    ``rows`` is the list of staging rows pulled from the API; the
    callbacks are invoked when the operator clicks the row / toolbar
    buttons. Pages mutate ``rows`` in-place when refreshing.
    """

    rows: list[StagedRunSummary]
    on_force_sync: Callable[[str], None] | None = None
    on_clear: Callable[[str], None] | None = None
    on_view_log: Callable[[str], None] | None = None
    on_clear_verified: Callable[[], None] | None = None


# ---------------------------------------------------------------------------
# Pure formatters (unit-testable without nicegui)
# ---------------------------------------------------------------------------


def format_bytes(value: int) -> str:
    """Render ``value`` as a binary unit string (KiB / MiB / ...).

    Returns a compact string suitable for table cells. The output uses
    the same notation across the codebase (see ``ui/components/tree.py``)
    so the column reads consistently with the rest of the app.
    """
    if value < 0:
        return "0 B"
    units = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
    f = float(value)
    idx = 0
    while f >= 1024 and idx < len(units) - 1:
        f /= 1024
        idx += 1
    if idx == 0:
        return f"{int(f)} {units[idx]}"
    return f"{f:.1f} {units[idx]}"


def format_elapsed(seconds: int) -> str:
    """Render ``seconds`` as a compact "Hh Mm Ss" / "Mm Ss" / "Ss" string."""
    if seconds < 0:
        seconds = 0
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h"


def state_pill_props(state: str) -> dict[str, str]:
    """Static badge props for a staging-state pill.

    Returns ``{label, color, background}`` so the renderer can build the
    badge without re-deriving colors. ``color`` defaults to the muted
    text token for unrecognised states (defensive -- the Pydantic
    Literal already constrains the values).
    """
    color = _STATE_COLORS.get(state, "var(--color-muted)")
    return {
        "label": state,
        "color": color,
        "background": "rgba(255,255,255,0.04)",
    }


def row_props(row: StagedRunSummary) -> dict[str, Any]:
    """Render-ready dict for one table row.

    Exposed for unit tests so the pure formatters can be asserted
    without instantiating NiceGUI elements.
    """
    return {
        "state": row.current_state,
        "state_pill": state_pill_props(row.current_state),
        "run": row.path,
        "run_label": _run_label(row.path),
        "equipment": row.equipment_id,
        "files": row.file_count,
        "bytes": format_bytes(row.byte_total),
        "elapsed": format_elapsed(row.elapsed_seconds_since_last_activity),
        "is_clearable": row.current_state == IngestState.SYNC_VERIFIED.value,
    }


def _run_label(run_path: str) -> str:
    """Return the leaf segment of ``run_path`` for the Run column."""
    if not run_path:
        return ""
    return run_path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


# ---------------------------------------------------------------------------
# NiceGUI renderer
# ---------------------------------------------------------------------------


def render_staging_dock(state: StagingDockState) -> Any:
    """Render the bottom-dock staging panel.

    Returns the NiceGUI element when the framework is importable (the
    common runtime case), or a plain dict describing the rendered shape
    when NiceGUI is unavailable (the unit-test path -- avoids forcing a
    headless Chromium just to check column ordering).
    """
    try:
        from nicegui import ui
    except Exception:
        return {
            "height_px": STAGING_DOCK_HEIGHT_PX,
            "columns": STAGING_TABLE_COLUMNS,
            "rows": [row_props(r) for r in state.rows],
        }

    with (
        ui.element("div")
        .props('data-testid="staging-dock"')
        .style(
            f"height: {STAGING_DOCK_HEIGHT_PX}px; "
            "border-top: 1px solid var(--color-rule); "
            "background: var(--color-bg); "
            "padding: var(--sp-2) var(--sp-4); "
            "overflow: auto;",
        ) as dock
    ):
        with ui.row().classes("items-center w-full").style("gap: var(--sp-3);"):
            ui.label("Staging").style(
                "font-family: var(--font-display); "
                "font-size: var(--text-sm); "
                "color: var(--color-heading); "
                "font-weight: 600;",
            )
            ui.space()
            verified_count = sum(
                1 for row in state.rows if row.current_state == IngestState.SYNC_VERIFIED.value
            )
            ui.button(
                f"Clear verified runs ({verified_count})",
                on_click=lambda _evt: _invoke(state.on_clear_verified),
            ).props('flat dense data-testid="staging-clear-verified"').style(
                "color: var(--color-body);"
            )

        # Header row.
        with (
            ui.row()
            .classes("w-full")
            .style(
                "gap: var(--sp-3); "
                "padding: var(--sp-1) 0; "
                "border-bottom: 1px solid var(--color-rule);",
            )
        ):
            for col in STAGING_TABLE_COLUMNS:
                ui.label(col).style(
                    "font-family: var(--font-mono); "
                    "font-size: var(--text-xs); "
                    "color: var(--color-muted);",
                )

        # Body rows.
        for idx, row in enumerate(state.rows):
            props = row_props(row)
            with (
                ui.row()
                .classes("items-center w-full")
                .props(f'data-testid="staging-row-{idx}"')
                .style(
                    "gap: var(--sp-3); padding: var(--sp-1) 0;",
                )
            ):
                ui.badge(props["state"]).style(
                    f"background: {props['state_pill']['background']}; "
                    f"color: {props['state_pill']['color']}; "
                    "padding: 2px 8px; border-radius: 999px;",
                )
                ui.label(props["run_label"]).style(
                    "font-family: var(--font-mono); "
                    "font-size: var(--text-xs); "
                    "color: var(--color-body);",
                )
                ui.label(props["equipment"]).style(
                    "font-family: var(--font-mono); "
                    "font-size: var(--text-xs); "
                    "color: var(--color-body);",
                )
                ui.label(str(props["files"])).style(
                    "font-family: var(--font-mono); "
                    "font-size: var(--text-xs); "
                    "color: var(--color-body);",
                )
                ui.label(props["bytes"]).style(
                    "font-family: var(--font-mono); "
                    "font-size: var(--text-xs); "
                    "color: var(--color-body);",
                )
                ui.label(props["elapsed"]).style(
                    "font-family: var(--font-mono); "
                    "font-size: var(--text-xs); "
                    "color: var(--color-body);",
                )
                with ui.row().style("gap: var(--sp-2);"):
                    ui.button(
                        "Force sync",
                        on_click=lambda _evt, p=row.path: _invoke(state.on_force_sync, p),
                    ).props(f'flat dense data-testid="staging-row-{idx}-force-sync"')
                    clear_button = ui.button(
                        "Clear",
                        on_click=lambda _evt, p=row.path: _invoke(state.on_clear, p),
                    ).props(f'flat dense data-testid="staging-row-{idx}-clear"')
                    if not props["is_clearable"]:
                        clear_button.disable()
                    ui.button(
                        "View log",
                        on_click=lambda _evt, p=row.path: _invoke(state.on_view_log, p),
                    ).props(f'flat dense data-testid="staging-row-{idx}-view-log"')
    return dock


def _invoke(handler: Callable[..., Any] | None, *args: Any) -> None:
    """Best-effort invoke a callback; ignore None so unit tests stay simple."""
    if handler is None:
        return
    handler(*args)
