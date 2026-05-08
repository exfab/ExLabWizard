"""Test-connection result panel (Frontend Spec §7.4.2).

A persistent inline panel below the Settings dialog's [Test connection]
button. Shape:

* **Result icon + headline** -- *"Connected"* (green check) or
  *"Connection failed"* (red X).
* **Detail line** -- one-line context (latency on success; reason on
  failure).
* **Show details** disclosure -- collapsed by default; expanded shows the
  full underlying response in a monospaced block.

The panel persists until the next test or until any field in the same
section is edited; on edit, ``mark_stale()`` flips a flag that the
caller renders as *"(may be stale; re-test to confirm)"*.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from exlab_wizard.logging import get_logger

_log = get_logger(__name__)


@dataclass(frozen=True)
class TestConnectionResult:
    """One result rendered in the panel."""

    success: bool
    headline: str
    detail: str
    raw: str


def panel_props(result: TestConnectionResult | None, *, stale: bool = False) -> dict[str, Any]:
    """Compute the rendered props for the panel.

    Returns a dict suitable for asserting in tests; the NiceGUI factory
    consumes the same dict.
    """

    if result is None:
        return {
            "visible": False,
            "headline": "",
            "detail": "",
            "raw": "",
            "success": False,
            "stale": False,
            "color_var": "--color-muted",
        }
    headline = result.headline
    if stale:
        headline = f"{headline} (may be stale; re-test to confirm)"
    return {
        "visible": True,
        "headline": headline,
        "detail": result.detail,
        "raw": result.raw,
        "success": result.success,
        "stale": stale,
        "color_var": "--color-success" if result.success else "--color-danger",
    }


def test_connection_panel(
    result: TestConnectionResult | None = None,
    *,
    stale: bool = False,
) -> Any:
    """Build the inline result panel.

    Returns a NiceGUI element, or the props dict when called outside of
    a NiceGUI app context.
    """

    props = panel_props(result, stale=stale)
    try:
        from nicegui import ui
    except Exception:
        return props

    if not props["visible"]:
        return ui.column().style("display: none;")

    column = (
        ui.column()
        .classes("w-full")
        .style(
            "gap: 0.25rem; "
            "padding: 0.75rem 1rem; "
            "border-radius: var(--radius); "
            "border: 1px solid var(--color-border); "
            "background: var(--color-surface);"
        )
    )
    with column:
        with ui.row().classes("items-center").style("gap: 0.5rem;"):
            ui.icon(
                "check_circle" if props["success"] else "error",
            ).style(f"color: var({props['color_var']});")
            ui.label(props["headline"]).style(
                "font-family: var(--font-body); font-size: var(--text-sm); font-weight: 500;"
            )
        ui.label(props["detail"]).style(
            "font-family: var(--font-mono); font-size: var(--text-xs); color: var(--color-muted);"
        )
        with ui.expansion("Show details", icon="expand_more").classes("w-full"):
            ui.code(props["raw"]).classes("w-full")
    return column
