"""Override pill (Frontend Spec §3.6.1, §11).

A pill that appears next to a run's title when an :class:`override` is
active in ``creation.json`` ``validation_overrides``. Uses
``--color-info`` so it's visually distinct from the warning-tier
*"Sync blocked"* surfaces.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from exlab_wizard.logging import get_logger
from exlab_wizard.ui import design

_log = get_logger(__name__)


def override_badge_props(*, active: bool = True) -> dict[str, str]:
    """Compute styling props for the override pill."""

    if not active:
        return {
            "label": "No override",
            "background": "rgba(136,146,164,0.08)",
            "text": design.COLOR_MUTED,
            "border": "rgba(136,146,164,0.20)",
        }
    return {
        "label": "Override active",
        "background": "rgba(86,180,233,0.10)",
        "text": design.BADGE_TEXT["sky"],
        "border": "rgba(86,180,233,0.25)",
    }


def override_badge(*, active: bool = True, on_click: Callable[[], None] | None = None) -> Any:
    """Build a clickable badge for the override state.

    Clicking opens the §11.5 override dialog in revoke mode (the wizard
    wires the click handler).
    """

    props = override_badge_props(active=active)
    try:
        from nicegui import ui
    except Exception:
        return props

    badge = ui.badge(props["label"]).style(
        f"background: {props['background']}; "
        f"color: {props['text']}; "
        f"border: 1px solid {props['border']}; "
        "border-radius: 9999px; "
        "padding: 0.2rem 0.55rem; "
        "font-family: var(--font-mono); "
        "font-size: 0.65rem; "
        "letter-spacing: 0.08em; "
        "text-transform: uppercase; "
        "font-weight: 500; "
        "cursor: pointer;"
    )
    if on_click is not None:
        badge.on("click", lambda _evt: on_click())
    return badge
