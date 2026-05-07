"""New Project Wizard (Frontend Spec §4).

Seven steps in a ``ui.stepper``:

1. LIMS Project picker (Backend §7.2 cache or offline catalogue).
2. Template Selection.
3. Equipment Selection.
4. Variable Form (auto-generated from ``copier.yml``).
5. README Form (mandatory core fields pinned at top).
6. Preview (validator gate; Frontend §4 step 6).
7. Confirm & Create (progress bar, error pane, success card).

The page is split into render-time-only logic (this module) and the
controller-side validation, which is delegated to the FastAPI session
endpoints. The UI's per-step validation is for UX immediacy; the backend
remains authoritative.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from exlab_wizard.logging import get_logger
from exlab_wizard.ui.components import session_progress

_log = get_logger(__name__)


PROJECT_WIZARD_STEPS: tuple[str, ...] = (
    "lims_project",
    "template",
    "equipment",
    "variables",
    "readme",
    "preview",
    "confirm",
)

PROJECT_STEP_TITLES: dict[str, str] = {
    "lims_project": "LIMS Project",
    "template": "Template",
    "equipment": "Equipment",
    "variables": "Variables",
    "readme": "README",
    "preview": "Preview",
    "confirm": "Confirm & Create",
}


# Pre-flight thresholds.
DISK_SPACE_MIN_BYTES = 100 * 1024 * 1024  # 100 MiB; Frontend §10.5.4


@dataclass
class ProjectWizardState:
    """Mutable state for the in-flight wizard."""

    active_step: str = PROJECT_WIZARD_STEPS[0]
    selected_lims_short_id: str | None = None
    selected_template: str | None = None
    selected_equipment: str | None = None
    template_variables: dict[str, Any] = field(default_factory=dict)
    readme_fields: dict[str, str] = field(default_factory=dict)
    validator_findings: list[dict[str, Any]] = field(default_factory=list)
    free_disk_bytes: int | None = None
    plugin_host_ok: bool = True


def can_advance(state: ProjectWizardState) -> bool:
    """Return ``True`` when the active step's preconditions are satisfied.

    Centralised here so the *Next* button enablement and any
    ``Cmd/Ctrl+Enter`` shortcut share a single rule set.
    """

    step = state.active_step
    if step == "lims_project":
        return state.selected_lims_short_id is not None
    if step == "template":
        return state.selected_template is not None
    if step == "equipment":
        return state.selected_equipment is not None
    if step == "variables":
        return len(state.template_variables) >= 0  # template-controlled
    if step == "readme":
        # Mandatory core fields per Frontend §6 + Backend §3.
        for field_id in ("label", "operator", "objective"):
            if not state.readme_fields.get(field_id):
                return False
        return True
    if step == "preview":
        return preview_step_clear(state)
    return True


def preview_step_clear(state: ProjectWizardState) -> bool:
    """Pre-flight checks for the Preview step (Frontend §10.5.4)."""

    if state.validator_findings:
        return False
    if not state.plugin_host_ok:
        return False
    return not (state.free_disk_bytes is not None and state.free_disk_bytes < DISK_SPACE_MIN_BYTES)


def disk_space_pre_flight_message(state: ProjectWizardState) -> str | None:
    """Return a copy-ready message when disk space is low; else ``None``."""

    if state.free_disk_bytes is None:
        return None
    if state.free_disk_bytes >= DISK_SPACE_MIN_BYTES:
        return None
    return "Insufficient disk space at <local_root>"


def render_project_wizard(
    *,
    state: ProjectWizardState | None = None,
    on_submit: Callable[[ProjectWizardState], None] | None = None,
) -> Any:
    """Render the seven-step project wizard.

    Returns the NiceGUI dialog (or, in tests, a payload describing the
    rendered steps).
    """

    s = state or ProjectWizardState()
    payload = {
        "steps": PROJECT_WIZARD_STEPS,
        "active": s.active_step,
        "can_advance": can_advance(s),
    }

    try:
        from nicegui import ui
    except Exception:
        return payload

    dialog = ui.dialog(value=True)
    with (
        dialog,
        ui.card().style(
            "min-width: 720px; "
            "padding: var(--sp-6); "
            "background: var(--color-surface); "
            "border-radius: var(--radius-md); "
            "box-shadow: var(--shadow-md);"
        ),
    ):
        with ui.row().classes("items-center w-full"):
            ui.label("New Project").style(
                "font-family: var(--font-display); "
                "font-size: var(--text-lg); "
                "color: var(--color-heading); "
                "font-weight: 600;"
            )
        with ui.stepper(value=s.active_step).props("vertical") as stepper:
            for step_id in PROJECT_WIZARD_STEPS:
                with ui.step(step_id, title=PROJECT_STEP_TITLES[step_id]):
                    ui.label(_step_helper_text(step_id, s)).style("color: var(--color-body);")
                    if step_id == "confirm":
                        session_progress.session_progress(
                            active_phase=None,
                        )
                    with ui.stepper_navigation():
                        ui.button(
                            "Back",
                            on_click=lambda _evt, sp=stepper: sp.previous(),
                        ).props("flat")
                        primary_label = "Create" if step_id == "confirm" else "Next"

                        def _on_primary(
                            _evt: Any,
                            sp: Any = stepper,
                            sid: str = step_id,
                        ) -> None:
                            if sid == "confirm" and on_submit is not None:
                                on_submit(s)
                            sp.next()

                        ui.button(primary_label, on_click=_on_primary).props("color=primary")
    return dialog


def _step_helper_text(step_id: str, state: ProjectWizardState) -> str:
    """Helper text rendered inside each stepper step."""

    if step_id == "lims_project":
        return "Pick the LIMS project this ExLab project will be tracked under."
    if step_id == "template":
        return "Pick a template scaffold for the project's directory layout."
    if step_id == "equipment":
        return "Pick the equipment that will host the project's runs."
    if step_id == "variables":
        return "Fill in the template's variables; project_name comes from LIMS."
    if step_id == "readme":
        return "Fill in label, operator, and objective. Add any extra fields you want."
    if step_id == "preview":
        if state.validator_findings:
            return "Validator detected unresolved tokens; go back and fix them."
        return "Review the resolved tree and README content."
    if step_id == "confirm":
        return "Click Create to write the directories and queue NAS sync."
    return ""
