"""Settings dialog (Frontend Spec §7).

Two-pane modal with a left vertical-nav and a right content area. Nine
sections; setup-incomplete mode auto-selects the first incomplete one.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from exlab_wizard.logging import get_logger
from exlab_wizard.ui.components import credential_field, test_connection_panel

_log = get_logger(__name__)


SETTINGS_SECTIONS: tuple[str, ...] = (
    "paths",
    "lims",
    "equipment",
    "nas_cleanup",
    "operators",
    "validator",
    "logging",
    "orchestrator",
    "application",
)

SECTION_TITLES: dict[str, str] = {
    "paths": "Paths",
    "lims": "LIMS",
    "equipment": "Equipment List",
    "nas_cleanup": "NAS Cleanup",
    "operators": "Operators",
    "validator": "Validator",
    "logging": "Logging",
    "orchestrator": "Orchestrator Mode",
    "application": "Application",
}


@dataclass
class SettingsState:
    """Mutable state for the dialog."""

    active_section: str = "paths"
    incomplete_sections: tuple[str, ...] = ()
    dirty_sections: set[str] = field(default_factory=set)
    pending_change_count: int = 0


def first_incomplete_section(incomplete: tuple[str, ...]) -> str | None:
    """Return the first section ID in canonical order that's incomplete."""

    for section in SETTINGS_SECTIONS:
        if section in incomplete:
            return section
    return None


def save_button_label(state: SettingsState) -> str:
    """Compute the *Save all* button label, including the badge count."""

    if state.incomplete_sections:
        return "Save and continue"
    if state.pending_change_count == 0:
        return "Save all changes"
    return f"Save all ({state.pending_change_count} changes)"


def section_has_warning(state: SettingsState, section: str) -> bool:
    """Return ``True`` when the sidebar should decorate ``section``."""

    return section in state.incomplete_sections


def section_is_dirty(state: SettingsState, section: str) -> bool:
    """Return ``True`` when ``section`` has uncommitted edits."""

    return section in state.dirty_sections


def render_settings_page(
    *,
    state: SettingsState | None = None,
    on_save: Callable[[SettingsState], None] | None = None,
    on_discard: Callable[[SettingsState], None] | None = None,
    on_select_section: Callable[[str], None] | None = None,
) -> Any:
    """Render the settings dialog.

    ``on_select_section`` is invoked when the operator clicks a sidebar
    nav row. The Phase 12 cut bound this to a no-op (the selection
    cycle is handled by the host page); the e2e harness wires it to a
    navigation hook so each section's body becomes assertable.
    """

    s = state or SettingsState()
    if s.incomplete_sections and s.active_section not in s.incomplete_sections:
        # Setup-incomplete mode: auto-select the first incomplete section
        # unless the caller has already pinned a specific section to render
        # (for example, after the operator clicks a sidebar nav row).
        first = first_incomplete_section(s.incomplete_sections)
        if first is not None:
            s = SettingsState(
                active_section=first,
                incomplete_sections=s.incomplete_sections,
                dirty_sections=s.dirty_sections,
                pending_change_count=s.pending_change_count,
            )

    payload = {
        "active": s.active_section,
        "save_label": save_button_label(s),
        "warnings": [section for section in SETTINGS_SECTIONS if section_has_warning(s, section)],
    }

    try:
        from nicegui import ui
    except Exception:
        return payload

    card = (
        ui.card()
        .props('data-testid="settings-dialog"')
        .style(
            "min-width: 880px; min-height: 600px; "
            "padding: var(--sp-4); "
            "background: var(--color-surface); "
            "border-radius: var(--radius-md); "
            "box-shadow: var(--shadow-md);"
        )
    )
    with card:
        if s.incomplete_sections:
            ui.label(
                "Setup incomplete. Configure the highlighted sections to start using ExLab-Wizard.",
            ).props('data-testid="settings-incomplete-banner"').style(
                "padding: 0.75rem 1rem; "
                "border-left: 4px solid var(--color-warning); "
                "background: rgba(230,159,0,0.07); "
                "border-radius: var(--radius);"
            )
        with ui.splitter(value=22).classes("w-full") as split:
            with split.before, ui.column().classes("w-full").style("gap: 0.25rem;"):
                for section in SETTINGS_SECTIONS:
                    nav_row = (
                        ui.row()
                        .classes("items-center w-full")
                        .props(f'data-testid="settings-nav-{section}"')
                        .style(
                            "padding: 0.5rem 0.75rem; cursor: pointer;",
                        )
                    )
                    if on_select_section is not None:
                        nav_row.on(
                            "click",
                            lambda _evt, sec=section: on_select_section(sec),
                        )
                    with nav_row:
                        ui.label(SECTION_TITLES[section]).style(
                            "font-family: var(--font-body); "
                            "font-size: var(--text-sm);"
                            + (
                                " font-weight: 600; color: var(--color-heading);"
                                if section == s.active_section
                                else " color: var(--color-body);"
                            )
                        )
                        if section_is_dirty(s, section):
                            ui.label("•").style("color: var(--color-info);")
                        if section_has_warning(s, section):
                            ui.icon("warning").style("color: var(--color-warning);")
            with split.after:
                _render_section_body(s.active_section)
        with (
            ui.row()
            .classes("items-center w-full justify-end")
            .style(
                "gap: var(--sp-3); padding-top: var(--sp-4);",
            )
        ):
            ui.button(
                "Discard all",
                on_click=lambda _evt: on_discard(s) if on_discard else None,
            ).props('flat data-testid="settings-discard"')
            ui.button(
                save_button_label(s),
                on_click=lambda _evt: on_save(s) if on_save else None,
            ).props('color=primary data-testid="settings-save"')
    return card


def _render_section_body(section: str) -> None:
    """Render the content for a single section.

    Each section is intentionally simple at this phase: the goal is to
    define the layout shell and let later phases bind real fields against
    the config schema.
    """

    from nicegui import ui

    with (
        ui.column()
        .classes("w-full")
        .props(f'data-testid="settings-section-{section}"')
        .style("gap: 0.5rem; padding: 0 1rem;")
    ):
        ui.label(SECTION_TITLES[section]).style(
            "font-family: var(--font-display); "
            "font-size: var(--text-md); "
            "color: var(--color-heading); "
            "font-weight: 600;"
        )

        if section == "paths":
            ui.input(label="Templates directory").props('data-testid="settings-paths-templates"')
            ui.input(label="Plugin directory").props('data-testid="settings-paths-plugin"')
            ui.input(label="Local data root").props('data-testid="settings-paths-local-root"')
        elif section == "lims":
            ui.input(label="Endpoint URL").props('data-testid="settings-lims-endpoint"')
            ui.input(label="Operator email").props('data-testid="settings-lims-email"')
            credential_field.credential_field(
                label="LIMS password",
                on_save=lambda v: None,
                on_clear=lambda: None,
            )
            ui.number(label="Cache TTL (hours)", value=24).props(
                'data-testid="settings-lims-cache-ttl"'
            )
            ui.input(label="Offline catalogue path").props(
                'data-testid="settings-lims-offline-path"'
            )
            test_connection_panel.test_connection_panel(None)
        elif section == "equipment":
            ui.label("Configured equipment will appear here. [+ Add equipment]").props(
                'data-testid="settings-equipment-empty"'
            )
            ui.input(label="Equipment ID (regex ^[A-Z][A-Z0-9_]*$, len 1-32)").props(
                'data-testid="settings-equipment-id"'
            )
            ui.button("Add equipment").props('data-testid="settings-equipment-add"')
        elif section == "nas_cleanup":
            ui.checkbox("Cleanup enabled")
            ui.number(label="Minimum verify passes", value=2)
            ui.number(label="Minimum age (hours)", value=24)
            ui.checkbox("Retain .exlab-wizard/ metadata")
        elif section == "operators":
            ui.label("Operator allowlist (chips)")
        elif section == "validator":
            ui.number(label="Max content-scan size (MiB)", value=5)
            ui.label("Scanned file extensions")
        elif section == "logging":
            ui.radio(["DEBUG", "INFO", "WARN", "ERROR"], value="INFO")
            ui.number(label="Central log size cap (MB)", value=10)
            ui.number(label="Rotated log copies kept", value=5)
        elif section == "orchestrator":
            ui.checkbox("Orchestrator mode enabled")
            ui.input(label="Workstation label")
            ui.input(label="Staging root")
        elif section == "application":
            ui.checkbox("Start ExLab-Wizard at login")
            ui.label("Show in system tray: available")
            ui.button("Quit ExLab-Wizard now").props("flat")
