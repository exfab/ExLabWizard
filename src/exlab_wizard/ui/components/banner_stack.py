"""Banner stack component (Frontend Spec §2.2.3).

Renders the active banners for a container. Stacking is capped at 2; a
3rd active banner collapses into a *"...and N more issues"* link.

Reads from :mod:`exlab_wizard.ui.notifications` instead of taking the
banner list as a constructor argument so any caller of
:func:`show_banner` automatically updates the stack on next render.
"""

from __future__ import annotations

from typing import Any

from exlab_wizard.logging import get_logger
from exlab_wizard.ui import notifications
from exlab_wizard.ui.notifications import BannerId, ContainerId, Severity

_log = get_logger(__name__)


_BANNER_STACK_MAX = 2


def banner_stack_props(container: ContainerId) -> dict[str, Any]:
    """Compute the visible / overflow split for a container.

    Returns a dict with ``visible`` (list of (BannerId, record) tuples
    capped at 2) and ``overflow_count`` (int).
    """

    items = notifications.list_active_banners(container=container)
    visible = items[:_BANNER_STACK_MAX]
    overflow = max(0, len(items) - _BANNER_STACK_MAX)
    return {"visible": visible, "overflow_count": overflow}


def _color_for_severity(severity: Severity) -> str:
    """Map a :class:`Severity` to its CSS variable token."""

    if severity is Severity.WARNING:
        return "--color-warning"
    if severity is Severity.DANGER:
        return "--color-danger"
    if severity is Severity.SUCCESS:
        return "--color-success"
    return "--color-info"


def banner_stack(container: ContainerId = ContainerId.GLOBAL) -> Any:
    """Build the banner stack UI for a container."""

    props = banner_stack_props(container)
    try:
        from nicegui import ui
    except Exception:
        return props

    column = (
        ui.column()
        .classes("w-full")
        .props(f'data-testid="banner-stack-{container.value}"')
        .style("gap: 0.5rem;")
    )
    with column:
        for banner_id, record in props["visible"]:
            severity = record["severity"]
            assert isinstance(severity, Severity)
            color = _color_for_severity(severity)
            with (
                ui.row()
                .classes("items-center w-full")
                .props(f'data-testid="banner-{banner_id.value}"')
                .style(
                    f"border-left: 4px solid var({color}); "
                    f"background: rgba(230,159,0,0.07); "
                    "padding: 0.75rem 1rem; "
                    "border-radius: var(--radius);"
                )
            ):
                with ui.column().style("gap: 0.125rem; flex-grow: 1;"):
                    ui.label(str(banner_id)).style(
                        "font-family: var(--font-body); "
                        "font-weight: 600; "
                        "font-size: var(--text-sm); "
                        f"color: var({color});"
                    )
                    ui.label(str(record["message"])).style(
                        "font-family: var(--font-body); "
                        "font-size: var(--text-sm); "
                        "color: var(--color-body); "
                        "opacity: 0.85;"
                    )
                action = record.get("action")
                if action is not None:
                    ui.button(
                        action.label,
                        on_click=lambda _evt, cb=action.on_click: cb(),
                    ).props("flat")
                if record.get("dismissible"):
                    ui.button(
                        icon="close",
                        on_click=lambda _evt, bid=banner_id: notifications.clear_banner(bid),
                    ).props("flat round dense")

        if props["overflow_count"] > 0:
            ui.label(
                f"...and {props['overflow_count']} more issues",
            ).style(
                "font-family: var(--font-body); "
                "font-size: var(--text-xs); "
                "color: var(--color-muted); "
                "cursor: pointer;"
            )
    return column


# Re-export commonly-used identifiers so wizards can compose without
# importing the notifications module directly when only the banner stack
# is needed.
__all__ = (
    "BannerId",
    "ContainerId",
    "Severity",
    "banner_stack",
    "banner_stack_props",
)
