"""Wizard session-progress bar (Frontend Spec §10.1, §9.3).

Drives a phase indicator on the wizard's Confirm & Create step from the
Backend §4.6.2 ``progress`` WebSocket event. The phase enum is fixed by
Backend §4.7; the labels come from Frontend §10.1.

When the active phase is ``running_plugins`` and the event carries
``current``/``total``, a sub-row is rendered per Frontend §9.3.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from exlab_wizard.logging import get_logger

_log = get_logger(__name__)


# Phase identifiers from Backend §4.7. The ordered tuple is the canonical
# render order for the progress bar.
PHASES: tuple[str, ...] = (
    "validating_inputs",
    "rendering_template",
    "running_plugins",
    "writing_cache",
    "post_validation",
    "queueing_sync",
)

PHASE_LABELS: dict[str, str] = {
    "validating_inputs": "Validating inputs",
    "rendering_template": "Rendering template",
    "running_plugins": "Running plugins",
    "writing_cache": "Writing cache",
    "post_validation": "Validating post-creation",
    "queueing_sync": "Queueing NAS sync",
}


@dataclass(frozen=True)
class PhaseRow:
    """One renderable row in the session-progress component."""

    phase: str
    label: str
    fraction: float
    is_active: bool
    is_done: bool


def compute_phase_rows(
    *,
    active_phase: str | None,
    completed: Iterable[str] = (),
) -> list[PhaseRow]:
    """Return one :class:`PhaseRow` per phase, in canonical order.

    A phase is *done* when it appears in ``completed``; *active* when it
    matches ``active_phase``; *pending* otherwise.

    The fractional value is 1.0 for done, 0.5 for active (a soft visual
    cue while the indeterminate sub-progress fills in), 0.0 for pending.
    """

    completed_set = set(completed)
    rows: list[PhaseRow] = []
    for phase in PHASES:
        is_done = phase in completed_set
        is_active = phase == active_phase and not is_done
        if is_done:
            fraction = 1.0
        elif is_active:
            fraction = 0.5
        else:
            fraction = 0.0
        rows.append(
            PhaseRow(
                phase=phase,
                label=PHASE_LABELS[phase],
                fraction=fraction,
                is_active=is_active,
                is_done=is_done,
            )
        )
    return rows


def session_progress(
    *,
    active_phase: str | None,
    completed: Iterable[str] = (),
    plugin_current: int | None = None,
    plugin_total: int | None = None,
    plugin_name: str | None = None,
) -> Any:
    """Build the progress bar for the Confirm & Create step.

    The wizard reaches into this and re-renders it on every progress
    event; in tests we just call :func:`compute_phase_rows` and assert on
    the data shape.
    """

    rows = compute_phase_rows(active_phase=active_phase, completed=completed)
    payload: dict[str, Any] = {
        "rows": [row.__dict__ for row in rows],
        "plugin_sub_row": None,
    }
    if (
        active_phase == "running_plugins"
        and plugin_current is not None
        and plugin_total is not None
    ):
        payload["plugin_sub_row"] = {
            "name": plugin_name or "plugin",
            "current": plugin_current,
            "total": plugin_total,
            "fraction": (plugin_current / plugin_total if plugin_total > 0 else 0.0),
        }

    try:
        from nicegui import ui
    except Exception:
        return payload

    column = ui.column().classes("w-full").style("gap: 0.5rem;")
    with column:
        for row in rows:
            with ui.row().classes("items-center w-full").style("gap: 0.5rem;"):
                style = "font-family: var(--font-body); font-size: var(--text-sm);"
                if row.is_done:
                    style += " color: var(--color-success);"
                elif row.is_active:
                    style += " color: var(--color-heading); font-weight: 500;"
                else:
                    style += " color: var(--color-muted);"
                ui.label(row.label).style(style)
                ui.linear_progress(value=row.fraction, show_value=False).props(
                    "color=primary track-color=rule"
                ).style("flex-grow: 1;")

        plugin_row = payload["plugin_sub_row"]
        if plugin_row is not None:
            with (
                ui.row().classes("items-center w-full").style("padding-left: 1.25rem; gap: 0.5rem;")
            ):
                ui.label(
                    f"{plugin_row['name']}  --  "
                    f"{plugin_row['current']} of {plugin_row['total']} plugins"
                ).style(
                    "font-family: var(--font-mono); "
                    "font-size: var(--text-xs); "
                    "color: var(--color-muted);"
                )
                ui.linear_progress(value=plugin_row["fraction"], show_value=False).props(
                    "color=info"
                ).style("flex-grow: 1;")
    return column
