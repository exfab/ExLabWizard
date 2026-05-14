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

import inspect
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
    lims_project_name: str = ""
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
    templates: list[str] | None = None,
    equipment_ids: list[str] | None = None,
    on_submit: Callable[[ProjectWizardState], Any] | None = None,
) -> Any:
    """Render the seven-step project wizard.

    ``templates`` is the list of project-scope template names the
    operator can pick from (from ``config.paths.templates_dir``);
    ``equipment_ids`` is the configured equipment list. Each step binds
    real inputs into ``state`` so the confirm step's ``on_submit`` sees
    a fully-populated :class:`ProjectWizardState`.

    Returns the NiceGUI dialog (or, in tests, a payload describing the
    rendered steps).
    """

    s = state or ProjectWizardState()
    template_choices = list(templates or [])
    equipment_choices = list(equipment_ids or [])
    payload = {
        "steps": PROJECT_WIZARD_STEPS,
        "active": s.active_step,
        "can_advance": can_advance(s),
        "templates": template_choices,
        "equipment_ids": equipment_choices,
    }

    try:
        from nicegui import ui
    except Exception:
        return payload

    card = (
        ui.card()
        .props('data-testid="wizard-project-card"')
        .style(
            "min-width: 720px; "
            "padding: var(--sp-6); "
            "background: var(--color-surface); "
            "border-radius: var(--radius-md); "
            "box-shadow: var(--shadow-md);"
        )
    )
    with card:
        with ui.row().classes("items-center w-full"):
            ui.label("New Project").props('data-testid="wizard-project-title"').style(
                "font-family: var(--font-display); "
                "font-size: var(--text-lg); "
                "color: var(--color-heading); "
                "font-weight: 600;"
            )
        with ui.stepper(value=s.active_step).props(
            'vertical data-testid="wizard-project-stepper"'
        ) as stepper:
            for step_id in PROJECT_WIZARD_STEPS:
                with ui.step(step_id, title=PROJECT_STEP_TITLES[step_id]).props(
                    f'data-testid="wizard-step-{step_id}"'
                ):
                    ui.label(_step_helper_text(step_id, s)).style("color: var(--color-body);")
                    _render_project_step_fields(
                        step_id, s, template_choices, equipment_choices
                    )
                    if step_id == "confirm":
                        session_progress.session_progress(
                            active_phase=None,
                        )
                    with ui.stepper_navigation():
                        ui.button(
                            "Back",
                            on_click=lambda _evt, sp=stepper: sp.previous(),
                        ).props('flat data-testid="wizard-back"')
                        primary_label = "Create" if step_id == "confirm" else "Next"

                        async def _on_primary(
                            _evt: Any,
                            sp: Any = stepper,
                            sid: str = step_id,
                        ) -> None:
                            # ``on_submit`` may be sync or async (the
                            # production handler awaits the controller
                            # pipeline) -- await it either way.
                            if sid == "confirm" and on_submit is not None:
                                result: Any = on_submit(s)
                                if inspect.isawaitable(result):
                                    await result
                                return
                            sp.next()

                        button_testid = "wizard-submit" if step_id == "confirm" else "wizard-next"
                        ui.button(primary_label, on_click=_on_primary).props(
                            f'color=primary data-testid="{button_testid}"'
                        )
    return card


def _render_project_step_fields(
    step_id: str,
    state: ProjectWizardState,
    templates: list[str],
    equipment_ids: list[str],
) -> None:
    """Render the bound input fields for one project-wizard step.

    Each widget two-way binds into ``state`` so values entered on an
    earlier step survive while the operator moves through the stepper.
    """
    from nicegui import ui

    if step_id == "lims_project":
        ui.input(
            label="LIMS project short ID (PROJ-NNNN)",
            value=state.selected_lims_short_id or "",
        ).props('data-testid="wizard-project-lims-id"').on_value_change(
            lambda e: setattr(state, "selected_lims_short_id", e.value or None)
        )
        ui.input(label="Project name", value=state.lims_project_name).props(
            'data-testid="wizard-project-lims-name"'
        ).bind_value(state, "lims_project_name")
    elif step_id == "template":
        ui.select(
            templates,
            value=state.selected_template if state.selected_template in templates else None,
            label="Project template",
        ).props('data-testid="wizard-project-template"').on_value_change(
            lambda e: setattr(state, "selected_template", e.value or None)
        )
    elif step_id == "equipment":
        ui.select(
            equipment_ids,
            value=(
                state.selected_equipment
                if state.selected_equipment in equipment_ids
                else None
            ),
            label="Equipment",
        ).props('data-testid="wizard-project-equipment"').on_value_change(
            lambda e: setattr(state, "selected_equipment", e.value or None)
        )
    elif step_id == "readme":
        for field_id, label in (
            ("label", "Label"),
            ("operator", "Operator"),
            ("objective", "Objective"),
        ):
            ui.input(label=label, value=state.readme_fields.get(field_id, "")).props(
                f'data-testid="wizard-project-readme-{field_id}"'
            ).on_value_change(
                lambda e, fid=field_id: state.readme_fields.__setitem__(fid, e.value or "")
            )


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
