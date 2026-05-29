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
STATE_FAILED = "failed"

_STATE_GLYPH: dict[str, str] = {
    STATE_RUNNING: "play_arrow",
    STATE_SUSPENDED: "pause",
    STATE_COMPLETED: "check",
    STATE_FAILED: "error",
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

    @classmethod
    def from_session(cls, session_id: str, session: Any) -> OperationRow:
        """Map a controller ``Session`` to a panel row.

        Collapses the §4.7 state machine onto the panel's three buckets:
        ``INPUT_REQUIRED`` -> suspended (offers Resume/Cancel), ``DONE`` ->
        completed, every other non-terminal state -> running. Shares
        ``project_identifier`` with the ``/operations`` route so both label
        rows identically (imported lazily to respect the controller/api
        import ordering).
        """
        from exlab_wizard.controller import SessionState, project_identifier
        from exlab_wizard.utils.time import dt_to_iso

        if session.state is SessionState.INPUT_REQUIRED:
            row_state = STATE_SUSPENDED
        elif session.state is SessionState.DONE:
            row_state = STATE_COMPLETED
        elif session.state is SessionState.FAILED:
            row_state = STATE_FAILED
        else:
            row_state = STATE_RUNNING
        request = session.request
        plugin = session.pending_input.get("plugin") if session.pending_input else None
        return cls(
            operation_id=session_id,
            state=row_state,
            started_at=dt_to_iso(session.created_at) if session.created_at is not None else "",
            equipment=getattr(request, "equipment_id", None) or "",
            project=project_identifier(request) or "",
            run=getattr(request, "label", None) or "",
            plugin=plugin,
        )


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

    state_priority = {STATE_SUSPENDED: 0, STATE_RUNNING: 1, STATE_FAILED: 2, STATE_COMPLETED: 3}
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
