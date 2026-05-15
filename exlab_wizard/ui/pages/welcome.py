"""Welcome card page (Frontend Spec §3.1.3).

Modal card shown exactly once on the first launch of the app on a
workstation. Three bullets describing the app, a *5-minute* setup
estimate, an autostart toggle defaulted on, and two buttons:

* ``[Get started]`` -- applies autostart and opens Settings in
  setup-incomplete mode (§7.14).
* ``Skip for now`` (text link) -- applies autostart and closes the card.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from exlab_wizard.logging import get_logger

_log = get_logger(__name__)


WELCOME_HEADLINE = "Welcome to ExLab-Wizard"
WELCOME_BULLETS: tuple[str, ...] = (
    # GUI/Orchestrator Redesign decision 2: reworded for the
    # multi-equipment file-explorer framing.
    "Acquire runs from any equipment you connect — and add more at any time.",
    "Watch files land in a live folder view, with sync status per file.",
    "Validates outputs and gates NAS sync on hard-tier findings.",
)
WELCOME_TIME_ESTIMATE = "Setup takes about 5 minutes."
WELCOME_AUTOSTART_LABEL = "Start ExLab-Wizard automatically when I log in."
WELCOME_AUTOSTART_HELPER = (
    "Recommended on lab workstations dedicated to acquisition. "
    "You can change this later in Settings -> Application."
)


@dataclass
class WelcomeCardSpec:
    """Render spec captured for unit-test assertions."""

    headline: str
    bullets: tuple[str, ...]
    time_estimate: str
    autostart_default_on: bool
    autostart_label: str
    autostart_helper: str
    primary_label: str
    secondary_label: str


def welcome_card_spec() -> WelcomeCardSpec:
    """Return the immutable spec used to render the welcome card."""

    return WelcomeCardSpec(
        headline=WELCOME_HEADLINE,
        bullets=WELCOME_BULLETS,
        time_estimate=WELCOME_TIME_ESTIMATE,
        autostart_default_on=True,
        autostart_label=WELCOME_AUTOSTART_LABEL,
        autostart_helper=WELCOME_AUTOSTART_HELPER,
        primary_label="Get started",
        secondary_label="Skip for now",
    )


def render_welcome_page(
    *,
    on_get_started: Callable[[bool], None],
    on_skip: Callable[[bool], None],
) -> Any:
    """Render the welcome card.

    ``on_get_started`` and ``on_skip`` are invoked with the autostart
    toggle's final value when the operator clicks the corresponding
    affordance.
    """

    spec = welcome_card_spec()
    try:
        from nicegui import ui
    except Exception:
        return spec

    autostart_value = {"on": spec.autostart_default_on}

    card = (
        ui.card()
        .props('data-testid="welcome-card"')
        .style(
            "max-width: 480px; "
            "padding: var(--sp-8); "
            "background: var(--color-surface); "
            "border-radius: var(--radius-lg); "
            "box-shadow: var(--shadow-lg);"  # modal card -- shadow-lg permitted
        )
    )
    with card:
        ui.label(spec.headline).props('data-testid="welcome-headline"').style(
            "font-family: var(--font-display); "
            "font-size: var(--text-2xl); "
            "color: var(--color-heading); "
            "font-weight: 600;"
        )
        for bullet in spec.bullets:
            ui.label(f"-- {bullet}").style(
                "font-family: var(--font-body); "
                "font-size: var(--text-sm); "
                "color: var(--color-body);"
            )
        ui.label(spec.time_estimate).style(
            "font-family: var(--font-body); font-size: var(--text-xs); color: var(--color-muted);"
        )

        def _on_toggle(evt: Any) -> None:
            autostart_value["on"] = bool(evt.value)

        ui.checkbox(
            spec.autostart_label,
            value=spec.autostart_default_on,
            on_change=_on_toggle,
        ).props('data-testid="welcome-autostart-toggle"')
        ui.label(spec.autostart_helper).style(
            "font-family: var(--font-body); font-size: var(--text-xs); color: var(--color-muted);"
        )

        with ui.row().classes("items-center w-full justify-end").style("gap: var(--sp-3);"):
            ui.button(
                spec.secondary_label,
                on_click=lambda _evt: on_skip(autostart_value["on"]),
            ).props('flat data-testid="welcome-skip-for-now"')
            ui.button(
                spec.primary_label,
                on_click=lambda _evt: on_get_started(autostart_value["on"]),
            ).props('color=primary data-testid="welcome-get-started"')
    return card
