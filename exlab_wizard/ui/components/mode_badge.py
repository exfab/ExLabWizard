"""Mode badge component (Frontend Spec §5.3, §6).

Shows the active wizard mode (experimental vs test). Experimental uses
``--color-navy``; test uses ``--color-warning`` per Frontend §2.1.4.
"""

from __future__ import annotations

from typing import Any

from exlab_wizard.logging import get_logger

_log = get_logger(__name__)

# Run kind values from Backend §3.
RUN_KIND_EXPERIMENTAL = "experimental"
RUN_KIND_TEST = "test"


def mode_badge_props(run_kind: str | None, *, label: str | None = None) -> dict[str, Any]:
    """Compute the badge styling props for a run kind.

    Returns a dict that wizards can spread onto a NiceGUI badge factory:

    * ``label`` -- displayed text, defaulting to a sensible label per kind.
    * ``color_var`` -- CSS variable token (``--color-navy`` or
      ``--color-warning``).
    * ``run_kind`` -- echoed back for tests.

    Test runs use the warning-tier hue; experimental runs use the navy
    primary. ``None`` (no run kind selected) defaults to experimental
    styling but with the label *"-- mode --"* so the wizard's title bar
    has something to display before the operator picks a mode.
    """

    if run_kind == RUN_KIND_TEST:
        return {
            "run_kind": RUN_KIND_TEST,
            "label": label or "Test",
            "color_var": "--color-warning",
        }
    if run_kind == RUN_KIND_EXPERIMENTAL:
        return {
            "run_kind": RUN_KIND_EXPERIMENTAL,
            "label": label or "Experimental",
            "color_var": "--color-navy",
        }
    return {
        "run_kind": None,
        "label": label or "-- mode --",
        "color_var": "--color-navy",
    }


def mode_badge(run_kind: str | None, *, label: str | None = None) -> Any:
    """Build a NiceGUI badge element for the given run kind.

    Lazy NiceGUI import keeps this module unit-testable; the returned
    object is a NiceGUI ``ui.badge`` instance when an app context is
    active, else a plain dict (useful for tests).
    """

    props = mode_badge_props(run_kind, label=label)
    try:
        from nicegui import ui
    except Exception:
        return props

    # Per Frontend §5.3 and DESIGN.md §05 badges, the badge background
    # uses a tinted fill and the text uses the darkened-AA variant.
    badge_kind = props["run_kind"] or "none"
    badge = (
        ui.badge(props["label"])
        .props(f'data-testid="mode-badge-{badge_kind}"')
        .style(
            f"background: var({props['color_var']}); "
            "color: var(--color-surface); "
            "font-family: var(--font-mono); "
            "font-size: var(--text-xs); "
            "padding: 0.2rem 0.55rem; "
            "border-radius: var(--radius-sm); "
            "letter-spacing: 0.08em; "
            "text-transform: uppercase;"
        )
    )
    return badge
