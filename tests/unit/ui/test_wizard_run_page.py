"""Tests for the New Run Wizard helpers (gaps not covered by test_pages).

``test_pages.py`` already exercises the title text, primary-button label
/ color, the preview path segments, and the project_equipment / readme
``can_advance`` rules. This file fills the gaps: the template / variables
/ preview / confirm / default-step ``can_advance`` branches, the
``run_dir_stem`` prefixes surfaced through ``preview_path_segments`` with
unset fields, and a render smoke check of the new ``templates`` /
``equipment_ids`` kwargs for both run kinds.
"""

from __future__ import annotations

# Prime the api package before importing ui.pages so the pre-existing
# orchestrator <-> api import order resolves cleanly (see test_mount.py).
import exlab_wizard.api.app  # noqa: F401  -- import order matters

from exlab_wizard.constants import RunKind
from exlab_wizard.ui.pages import wizard_run
from exlab_wizard.ui.pages.wizard_run import (
    RunWizardState,
    can_advance,
    preview_path_segments,
    primary_button_color,
    primary_button_label,
    render_run_wizard,
    title_text,
)

# ---------------------------------------------------------------------------
# can_advance -- branches not covered by test_pages.py
# ---------------------------------------------------------------------------


def test_can_advance_template_step_requires_selection() -> None:
    state = RunWizardState(run_kind=RunKind.EXPERIMENTAL, active_step="template")
    assert can_advance(state) is False
    state.selected_template = "run-default"
    assert can_advance(state) is True


def test_can_advance_variables_step_always_true() -> None:
    state = RunWizardState(run_kind=RunKind.EXPERIMENTAL, active_step="variables")
    assert can_advance(state) is True


def test_can_advance_preview_step_blocks_on_validator_findings() -> None:
    state = RunWizardState(run_kind=RunKind.EXPERIMENTAL, active_step="preview")
    assert can_advance(state) is True
    state.validator_findings = [{"rule": "Placeholder", "matched": "<x>"}]
    assert can_advance(state) is False


def test_can_advance_confirm_step_always_true() -> None:
    state = RunWizardState(run_kind=RunKind.TEST, active_step="confirm")
    assert can_advance(state) is True


def test_can_advance_default_step_is_project_equipment() -> None:
    state = RunWizardState(run_kind=RunKind.EXPERIMENTAL)
    assert state.active_step == "project_equipment"
    assert can_advance(state) is False
    state.selected_project_short_id = "PROJ-1"
    assert can_advance(state) is False  # equipment still missing
    state.selected_equipment = "CONFOCAL_01"
    assert can_advance(state) is True


def test_can_advance_readme_blocks_on_partial_core_fields() -> None:
    state = RunWizardState(run_kind=RunKind.TEST, active_step="readme")
    state.readme_fields = {"label": "x", "operator": "alex"}  # objective missing
    assert can_advance(state) is False
    state.readme_fields["objective"] = "y"
    assert can_advance(state) is True


# ---------------------------------------------------------------------------
# RunWizardState -- run_kind bound at construction
# ---------------------------------------------------------------------------


def test_state_run_kind_is_bound_and_drives_helpers() -> None:
    exp = RunWizardState(run_kind=RunKind.EXPERIMENTAL)
    test = RunWizardState(run_kind=RunKind.TEST)

    assert exp.run_kind is RunKind.EXPERIMENTAL
    assert test.run_kind is RunKind.TEST
    assert title_text(exp) != title_text(test)
    assert primary_button_color(exp) == "primary"
    assert primary_button_color(test) == "warning"
    assert primary_button_label(test) == "Create test run"


# ---------------------------------------------------------------------------
# preview_path_segments -- run_dir_stem prefixes with unset fields
# ---------------------------------------------------------------------------


def test_preview_path_segments_experimental_uses_run_prefix() -> None:
    state = RunWizardState(run_kind=RunKind.EXPERIMENTAL)
    result = preview_path_segments(state, run_date="2026-05-13")
    segments = result["segments"]
    # Unset project/equipment fall back to placeholders.
    assert segments[0] == "<equipment>"
    assert segments[1] == "<project>"
    assert segments[-1] == "Run_2026-05-13"
    assert result["warning_indices"] == ()


def test_preview_path_segments_test_uses_testrun_prefix_and_folder() -> None:
    state = RunWizardState(
        run_kind=RunKind.TEST,
        selected_equipment="EM_02",
        selected_project_short_id="PROJ-9",
    )
    result = preview_path_segments(state, run_date="2026-05-13")
    segments = result["segments"]
    assert segments == ["EM_02", "PROJ-9", "TestRuns", "TestRun_2026-05-13"]
    assert result["warning_indices"] == (2, 3)


# ---------------------------------------------------------------------------
# render_run_wizard -- new templates / equipment_ids kwargs
# ---------------------------------------------------------------------------


def test_render_run_wizard_experimental_with_choices_does_not_raise() -> None:
    out = render_run_wizard(
        state=RunWizardState(run_kind=RunKind.EXPERIMENTAL),
        templates=["run-default", "sweep"],
        equipment_ids=["CONFOCAL_01"],
        on_submit=lambda _s: None,
    )
    assert out is not None


def test_render_run_wizard_test_with_choices_does_not_raise() -> None:
    out = render_run_wizard(
        state=RunWizardState(run_kind=RunKind.TEST),
        templates=["run-test"],
        equipment_ids=["EM_02"],
        on_submit=lambda _s: None,
    )
    assert out is not None


def test_render_run_wizard_with_empty_choices_does_not_raise() -> None:
    out = render_run_wizard(
        state=RunWizardState(run_kind=RunKind.EXPERIMENTAL),
        templates=[],
        equipment_ids=[],
    )
    assert out is not None


def test_wizard_run_step_titles_cover_every_step() -> None:
    assert set(wizard_run.RUN_STEP_TITLES) == set(wizard_run.RUN_WIZARD_STEPS)
