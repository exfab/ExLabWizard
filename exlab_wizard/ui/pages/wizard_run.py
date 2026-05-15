"""New Run Wizard (Frontend Spec §5).

Six-step wizard with mode bound at construction (experimental vs test).
The mode is a single flag and cannot be changed mid-session; a misclicked
mode is resolved by closing and reopening the wizard.

Steps:

1. Project + Equipment.
2. Template Selection (filtered by ``_exlab_run_scope``).
3. Variable Form.
4. README Form.
5. Preview (validator gate).
6. Confirm & Create.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from exlab_wizard.constants import RunKind
from exlab_wizard.logging import get_logger
from exlab_wizard.paths import run_dir_stem
from exlab_wizard.ui.components import mode_badge, session_progress

if TYPE_CHECKING:
    from exlab_wizard.ui.pages.templates import TemplateQuestion

_log = get_logger(__name__)


RUN_WIZARD_STEPS: tuple[str, ...] = (
    "project_equipment",
    "template",
    "variables",
    "readme",
    "preview",
    "confirm",
)

RUN_STEP_TITLES: dict[str, str] = {
    "project_equipment": "Project + Equipment",
    "template": "Template",
    "variables": "Variables",
    "readme": "README",
    "preview": "Preview",
    "confirm": "Confirm & Create",
}


@dataclass
class RunWizardState:
    """Mutable state for the in-flight run wizard."""

    run_kind: RunKind  # bound at construction
    active_step: str = RUN_WIZARD_STEPS[0]
    selected_project_name: str | None = None
    selected_equipment: str | None = None
    selected_template: str | None = None
    template_variables: dict[str, Any] = field(default_factory=dict)
    readme_fields: dict[str, str] = field(default_factory=dict)
    validator_findings: list[dict[str, Any]] = field(default_factory=list)


def title_text(state: RunWizardState) -> str:
    """Title-bar text per Frontend §5.1."""

    if state.run_kind == RunKind.TEST:
        return "New Test Run"
    return "New Run -- Experimental"


def primary_button_label(state: RunWizardState) -> str:
    """Primary button label on the Confirm & Create step (Frontend §5.2)."""

    return "Create test run" if state.run_kind == RunKind.TEST else "Create run"


def primary_button_color(state: RunWizardState) -> str:
    """Primary button color hint per Frontend §5.3."""

    return "warning" if state.run_kind == RunKind.TEST else "primary"


def preview_path_segments(state: RunWizardState, *, run_date: str) -> dict[str, Any]:
    """Compute the destination-path segments for the Preview step.

    Test runs put the run inside a ``TestRuns/`` folder with a
    ``TestRun_`` leaf prefix that is highlighted in warning-tier color.
    """

    if state.run_kind == RunKind.TEST:
        return {
            "segments": [
                state.selected_equipment or "<equipment>",
                state.selected_project_name or "<project>",
                "TestRuns",
                run_dir_stem(run_date, test=True),
            ],
            "warning_indices": (2, 3),
        }
    return {
        "segments": [
            state.selected_equipment or "<equipment>",
            state.selected_project_name or "<project>",
            run_dir_stem(run_date),
        ],
        "warning_indices": (),
    }


def can_advance(state: RunWizardState) -> bool:
    """Return ``True`` when the active step's preconditions hold."""

    step = state.active_step
    if step == "project_equipment":
        return state.selected_project_name is not None and state.selected_equipment is not None
    if step == "template":
        return state.selected_template is not None
    if step == "variables":
        return True
    if step == "readme":
        for field_id in ("label", "operator", "objective"):
            if not state.readme_fields.get(field_id):
                return False
        return True
    if step == "preview":
        return not state.validator_findings
    return True


def render_run_wizard(
    *,
    state: RunWizardState,
    templates: list[str] | None = None,
    equipment_ids: list[str] | None = None,
    template_questions: dict[str, list[TemplateQuestion]] | None = None,
    on_submit: Callable[[RunWizardState], Any] | None = None,
    on_cancel: Callable[[], None] | None = None,
) -> Any:
    """Render the six-step run wizard.

    ``templates`` lists run-scope template names appropriate to the
    run kind; ``equipment_ids`` is the configured equipment list;
    ``template_questions`` maps each template name to its parsed
    ``copier.yml`` questions (drives the dynamic Variables step). Each
    step binds real inputs into ``state`` so the confirm step's
    ``on_submit`` sees a fully-populated :class:`RunWizardState`.
    """

    template_choices = list(templates or [])
    equipment_choices = list(equipment_ids or [])
    questions_map = template_questions or {}
    payload = {
        "title": title_text(state),
        "mode_badge": mode_badge.mode_badge_props(state.run_kind),
        "steps": RUN_WIZARD_STEPS,
        "active": state.active_step,
        "primary_label": primary_button_label(state),
        "primary_color": primary_button_color(state),
        "templates": template_choices,
        "equipment_ids": equipment_choices,
        "template_questions": {k: [q.key for q in v] for k, v in questions_map.items()},
    }

    try:
        from nicegui import ui
    except Exception:
        return payload

    from exlab_wizard.ui.pages.templates import render_question_field

    @ui.refreshable
    def _variables_panel() -> None:
        """Dynamic Copier-variable form for the currently-picked template."""
        questions = questions_map.get(state.selected_template or "", [])
        if not questions:
            ui.label("This template declares no variables; Copier defaults are used.").props(
                'data-testid="wizard-run-variables-empty"'
            ).style("color: var(--color-muted);")
            return
        for question in questions:
            render_question_field(
                question, state.template_variables, testid_prefix="wizard-run-var"
            )

    card = (
        ui.card()
        .props(f'data-testid="wizard-run-card-{state.run_kind}"')
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
            ui.label(title_text(state)).props('data-testid="wizard-run-title"').style(
                "font-family: var(--font-display); "
                "font-size: var(--text-lg); "
                "color: var(--color-heading); "
                "font-weight: 600;"
            )
            mode_badge.mode_badge(state.run_kind)
        with ui.stepper(value=state.active_step).props(
            'vertical data-testid="wizard-run-stepper"'
        ) as stepper:
            for step_id in RUN_WIZARD_STEPS:
                with ui.step(step_id, title=RUN_STEP_TITLES[step_id]).props(
                    f'data-testid="wizard-run-step-{step_id}"'
                ):
                    ui.label(_step_helper_text(step_id, state)).style("color: var(--color-body);")
                    if step_id == "variables":
                        _variables_panel()
                    else:
                        _render_run_step_fields(
                            step_id,
                            state,
                            template_choices,
                            equipment_choices,
                            on_template_change=_variables_panel.refresh,
                        )
                    if step_id == "confirm":
                        session_progress.session_progress(active_phase=None)
                    with ui.stepper_navigation():
                        # The first step has nowhere to step back to, so
                        # Cancel is its only exit -- rendering a dead Back
                        # button there is the bug being fixed here.
                        if step_id != RUN_WIZARD_STEPS[0]:
                            ui.button(
                                "Back",
                                on_click=lambda _evt, sp=stepper: sp.previous(),
                            ).props('flat data-testid="wizard-run-back"')
                        if on_cancel is not None:
                            ui.button(
                                "Cancel",
                                on_click=lambda _evt: on_cancel(),
                            ).props('flat data-testid="wizard-run-cancel"')
                        primary_label = (
                            primary_button_label(state) if step_id == "confirm" else "Next"
                        )

                        async def _on_primary(
                            _evt: Any,
                            sp: Any = stepper,
                            sid: str = step_id,
                        ) -> None:
                            # ``on_submit`` may be sync or async; await
                            # it either way (the production handler
                            # awaits the controller pipeline).
                            if sid == "confirm" and on_submit is not None:
                                result: Any = on_submit(state)
                                if inspect.isawaitable(result):
                                    await result
                                return
                            sp.next()

                        button_testid = (
                            "wizard-run-submit" if step_id == "confirm" else "wizard-run-next"
                        )
                        ui.button(primary_label, on_click=_on_primary).props(
                            f'color={primary_button_color(state)} data-testid="{button_testid}"'
                        )
    return card


def _render_run_step_fields(
    step_id: str,
    state: RunWizardState,
    templates: list[str],
    equipment_ids: list[str],
    *,
    on_template_change: Callable[..., Any],
) -> None:
    """Render the bound input fields for one run-wizard step.

    The "variables" step is rendered by the caller's refreshable panel,
    not here.
    """
    from nicegui import ui

    if step_id == "project_equipment":
        ui.input(
            label="Parent project name",
            value=state.selected_project_name or "",
        ).props('data-testid="wizard-run-project-name"').on_value_change(
            lambda e: setattr(state, "selected_project_name", e.value or None)
        )
        ui.select(
            equipment_ids,
            value=(state.selected_equipment if state.selected_equipment in equipment_ids else None),
            label="Equipment",
        ).props('data-testid="wizard-run-equipment"').on_value_change(
            lambda e: setattr(state, "selected_equipment", e.value or None)
        )
    elif step_id == "template":

        def _on_template(event: Any) -> None:
            state.selected_template = event.value or None
            on_template_change()

        ui.select(
            templates,
            value=state.selected_template if state.selected_template in templates else None,
            label="Run template",
        ).props('data-testid="wizard-run-template"').on_value_change(_on_template)
    elif step_id == "readme":
        for field_id, label in (
            ("label", "Label"),
            ("operator", "Operator"),
            ("objective", "Objective"),
        ):
            ui.input(label=label, value=state.readme_fields.get(field_id, "")).props(
                f'data-testid="wizard-run-readme-{field_id}"'
            ).on_value_change(
                lambda e, fid=field_id: state.readme_fields.__setitem__(fid, e.value or "")
            )


def _step_helper_text(step_id: str, state: RunWizardState) -> str:
    """Helper text per step."""

    if step_id == "project_equipment":
        return "Pick the parent project and equipment for this run."
    if step_id == "template":
        return "Pick a run-scope template appropriate to the run kind."
    if step_id == "variables":
        return "Fill in the template's variables; run_date is auto-filled."
    if step_id == "readme":
        return "Fill in label, operator, and objective."
    if step_id == "preview":
        if state.run_kind == RunKind.TEST:
            return (
                "TestRuns/ and TestRun_ are highlighted; this run is excluded "
                "from automated analysis."
            )
        return "Review the resolved destination path and README."
    if step_id == "confirm":
        return primary_button_label(state)
    return ""
