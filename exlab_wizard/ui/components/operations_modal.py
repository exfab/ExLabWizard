"""In-flight operations panel (Frontend Spec §9.5).

A modal reachable from the Sync segment of the bottom status bar (when
any session is suspended in ``INPUT_REQUIRED``) and from a toolbar
``[Operations...]`` button. Backed by ``GET /api/v1/operations``
(Backend §4.6.1).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from exlab_wizard.logging import get_logger

_log = get_logger(__name__)


STATE_RUNNING = "running"
STATE_SUSPENDED = "suspended"
STATE_COMPLETED = "completed"

_STATE_GLYPH: dict[str, str] = {
    STATE_RUNNING: "play_arrow",
    STATE_SUSPENDED: "pause",
    STATE_COMPLETED: "check",
}


@dataclass(frozen=True)
class OperationRow:
    """A single row in the operations panel."""

    operation_id: str
    state: str
    started_at: str
    equipment: str
    project: str
    run: str
    plugin: str | None = None


def operation_columns() -> list[dict[str, Any]]:
    """Column definitions for the NiceGUI table (Frontend §9.5)."""

    return [
        {"name": "state", "label": "State", "field": "state", "align": "left"},
        {"name": "started_at", "label": "Started", "field": "started_at", "align": "left"},
        {"name": "equipment", "label": "Equipment", "field": "equipment", "align": "left"},
        {"name": "project", "label": "Project", "field": "project", "align": "left"},
        {"name": "run", "label": "Run", "field": "run", "align": "left"},
        {"name": "plugin", "label": "Plugin", "field": "plugin", "align": "left"},
    ]


def sort_rows(rows: list[OperationRow]) -> list[OperationRow]:
    """Suspended rows first (oldest first), then running, then completed.

    Per Frontend §9.5: suspended-row default-sort is by Started-at oldest
    first so the operator clears the longest-pending input first.
    """

    state_priority = {STATE_SUSPENDED: 0, STATE_RUNNING: 1, STATE_COMPLETED: 2}
    return sorted(
        rows,
        key=lambda r: (state_priority.get(r.state, 99), r.started_at),
    )


def state_glyph(state: str) -> str:
    """Map an operation state to its NiceGUI icon name."""

    return _STATE_GLYPH.get(state, "circle")


def operations_modal(
    rows: list[OperationRow],
    *,
    on_resume: Callable[[str], None],
    on_cancel: Callable[[str], None],
    on_view_log: Callable[[str], None],
) -> Any:
    """Build the operations modal."""

    sorted_rows = sort_rows(rows)
    payload = {
        "columns": operation_columns(),
        "rows": [r.__dict__ for r in sorted_rows],
    }

    try:
        from nicegui import ui
    except Exception:
        return payload

    dialog = ui.dialog()
    with (
        dialog,
        ui.card().style(
            "min-width: 720px; "
            "padding: 1.5rem; "
            "background: var(--color-surface); "
            "border-radius: var(--radius-md); "
            "box-shadow: var(--shadow-md);"
        ),
    ):
        ui.label("Operations").style(
            "font-family: var(--font-display); "
            "font-size: var(--text-lg); "
            "color: var(--color-heading); "
            "font-weight: 600;"
        )
        for row in sorted_rows:
            with (
                ui.row()
                .classes("items-center w-full")
                .style(
                    "padding: 0.5rem 0; border-bottom: 1px solid var(--color-rule); gap: 0.5rem;"
                )
            ):
                ui.icon(state_glyph(row.state)).style("color: var(--color-muted); font-size: 1rem;")
                ui.label(row.started_at).style(
                    "font-family: var(--font-mono); "
                    "font-size: var(--text-xs); "
                    "color: var(--color-muted);"
                )
                ui.label(row.equipment).style("font-family: var(--font-body);")
                ui.label(row.project).style("font-family: var(--font-body);")
                ui.label(row.run).style("font-family: var(--font-body);")
                if row.plugin:
                    ui.label(row.plugin).style(
                        "font-family: var(--font-mono); font-size: var(--text-xs);"
                    )
                if row.state == STATE_SUSPENDED:
                    ui.button(
                        "Resume",
                        on_click=lambda _evt, oid=row.operation_id: on_resume(oid),
                    ).props("flat")
                    ui.button(
                        "Cancel",
                        on_click=lambda _evt, oid=row.operation_id: on_cancel(oid),
                    ).props("flat")
                ui.button(
                    "View log",
                    on_click=lambda _evt, oid=row.operation_id: on_view_log(oid),
                ).props("flat")
    return dialog
