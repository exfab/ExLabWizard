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

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from exlab_wizard.constants import RunKind
from exlab_wizard.logging import get_logger
from exlab_wizard.paths import run_dir_stem
from exlab_wizard.ui.components import mode_badge, session_progress

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
    selected_project_short_id: str | None = None
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
                state.selected_project_short_id or "<project>",
                "TestRuns",
                run_dir_stem(run_date, test=True),
            ],
            "warning_indices": (2, 3),
        }
    return {
        "segments": [
            state.selected_equipment or "<equipment>",
            state.selected_project_short_id or "<project>",
            run_dir_stem(run_date),
        ],
        "warning_indices": (),
    }


def can_advance(state: RunWizardState) -> bool:
    """Return ``True`` when the active step's preconditions hold."""

    step = state.active_step
    if step == "project_equipment":
        return state.selected_project_short_id is not None and state.selected_equipment is not None
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
    on_submit: Callable[[RunWizardState], None] | None = None,
) -> Any:
    """Render the six-step run wizard."""

    payload = {
        "title": title_text(state),
        "mode_badge": mode_badge.mode_badge_props(state.run_kind),
        "steps": RUN_WIZARD_STEPS,
        "active": state.active_step,
        "primary_label": primary_button_label(state),
        "primary_color": primary_button_color(state),
    }

    try:
        from nicegui import ui
    except Exception:
        return payload

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
                    if step_id == "confirm":
                        session_progress.session_progress(active_phase=None)
                    with ui.stepper_navigation():
                        ui.button(
                            "Back",
                            on_click=lambda _evt, sp=stepper: sp.previous(),
                        ).props('flat data-testid="wizard-run-back"')
                        primary_label = (
                            primary_button_label(state) if step_id == "confirm" else "Next"
                        )

                        def _on_primary(
                            _evt: Any,
                            sp: Any = stepper,
                            sid: str = step_id,
                        ) -> None:
                            if sid == "confirm" and on_submit is not None:
                                on_submit(state)
                            sp.next()

                        button_testid = (
                            "wizard-run-submit" if step_id == "confirm" else "wizard-run-next"
                        )
                        ui.button(primary_label, on_click=_on_primary).props(
                            f'color={primary_button_color(state)} data-testid="{button_testid}"'
                        )
    return card


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
