"""Empty-state placeholder (GUI/Orchestrator Redesign §4 polish, Phase 5).

A centred icon above a one-line muted hint, shown by the explorer panes when
there is nothing to display -- no folder selected, an empty folder, or no node
metadata. It replaces the earlier bare one-line labels so each empty region
reads as an intentional placeholder rather than stray text, while keeping the
same ``data-testid`` the flows assert on.

The helper is the only NiceGUI-touching surface; outside an app context (unit
tests) it yields ``None`` and renders nothing, mirroring the other components'
NiceGUI-optional pattern. Colours / spacing resolve to design tokens with a
literal fallback so the placeholder still renders if ``register_theme`` is
skipped on a route.
"""

from __future__ import annotations

from typing import Any


def empty_state(*, icon: str, message: str, testid: str) -> Any:
    """Render a centred icon + hint placeholder; return the column element.

    ``icon`` is a Material-icon name (the same set
    :func:`sync_status_icon` draws from); ``message`` is the one-line hint.
    ``testid`` is set on the outer column so a flow that asserted on the old
    bare label's ``data-testid`` keeps matching (placed once, to avoid a
    duplicate-testid strict-mode clash).

    Returns ``None`` outside a NiceGUI app context so the call is a safe no-op
    in unit tests that don't spin up NiceGUI.
    """
    try:
        from nicegui import ui
    except Exception:
        return None

    with (
        ui.column()
        .classes("items-center")
        .style(
            "width: 100%; gap: var(--sp-2, 0.5rem); "
            "padding: var(--sp-6, 1.5rem) var(--sp-3, 0.75rem); "
            "color: var(--color-muted, #8892a4); text-align: center;"
        )
        .props(f'data-testid="{testid}"') as column
    ):
        ui.icon(icon).style("font-size: 2rem; color: var(--color-muted, #8892a4); opacity: 0.7;")
        ui.label(message).style(
            "color: var(--color-muted, #8892a4); font-size: var(--text-sm, 0.8125rem);"
        )
    return column
