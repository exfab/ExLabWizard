"""Test-run pill (Frontend Spec §3.2, §3.6.1).

A small *"Test"* pill rendered next to the run label in the left tree, the
detail-pane title bar, and orchestrator-mode staging rows. Uses
``--color-warning`` per Frontend §2.1.4 with the darkened orange text
variant for WCAG AA contrast on white.
"""

from __future__ import annotations

from typing import Any

from exlab_wizard.logging import get_logger
from exlab_wizard.ui import design

_log = get_logger(__name__)


def test_run_badge_props() -> dict[str, str]:
    """Static styling for the *"Test"* pill (Frontend §3.6.1)."""

    return {
        "label": "Test",
        "background": "rgba(230,159,0,0.10)",  # warning tinted background
        "text": design.BADGE_TEXT["orange"],
        "border": "rgba(230,159,0,0.25)",
    }


def test_run_badge() -> Any:
    """Build a NiceGUI badge for a test run."""

    props = test_run_badge_props()
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
        "font-weight: 500;"
    )
    return badge
