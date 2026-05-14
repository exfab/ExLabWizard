"""Tests for the New Project Wizard helpers (gaps not covered by test_pages).

``test_pages.py`` already exercises the LIMS / template / readme / preview
``can_advance`` rules and the preview pre-flight checks. This file fills
the remaining gaps: the equipment / variables / confirm / default-step
``can_advance`` branches, the ``disk_space_pre_flight_message`` "all
clear" path, the ``lims_project_name`` field, and a render smoke check
of the new ``templates`` / ``equipment_ids`` kwargs.
"""

from __future__ import annotations

# Prime the api package before importing ui.pages so the pre-existing
# orchestrator <-> api import order resolves cleanly (see test_mount.py).
import exlab_wizard.api.app  # noqa: F401  -- import order matters

from exlab_wizard.ui.pages import wizard_project
from exlab_wizard.ui.pages.wizard_project import (
    DISK_SPACE_MIN_BYTES,
    ProjectWizardState,
    can_advance,
    disk_space_pre_flight_message,
    render_project_wizard,
)

# ---------------------------------------------------------------------------
# can_advance -- branches not covered by test_pages.py
# ---------------------------------------------------------------------------


def test_can_advance_equipment_step_requires_selection() -> None:
    state = ProjectWizardState(active_step="equipment")
    assert can_advance(state) is False
    state.selected_equipment = "CONFOCAL_01"
    assert can_advance(state) is True


def test_can_advance_variables_step_always_true() -> None:
    # Variables are template-controlled; the step never blocks Next.
    state = ProjectWizardState(active_step="variables")
    assert can_advance(state) is True
    state.template_variables = {"sample": "x"}
    assert can_advance(state) is True


def test_can_advance_confirm_step_always_true() -> None:
    assert can_advance(ProjectWizardState(active_step="confirm")) is True


def test_can_advance_default_step_is_lims_project() -> None:
    # The dataclass default active_step is the first step (lims_project).
    state = ProjectWizardState()
    assert state.active_step == "lims_project"
    assert can_advance(state) is False
    state.selected_lims_short_id = "PROJ-42"
    assert can_advance(state) is True


def test_can_advance_readme_blocks_on_partial_core_fields() -> None:
    state = ProjectWizardState(active_step="readme")
    state.readme_fields = {"label": "x", "operator": "alex"}  # objective missing
    assert can_advance(state) is False
    state.readme_fields["objective"] = "y"
    assert can_advance(state) is True


# ---------------------------------------------------------------------------
# disk_space_pre_flight_message -- the "all clear" / "unknown" paths
# ---------------------------------------------------------------------------


def test_disk_space_message_none_when_unknown() -> None:
    state = ProjectWizardState(free_disk_bytes=None)
    assert disk_space_pre_flight_message(state) is None


def test_disk_space_message_none_when_ample() -> None:
    state = ProjectWizardState(free_disk_bytes=DISK_SPACE_MIN_BYTES)
    assert disk_space_pre_flight_message(state) is None


def test_disk_space_message_present_just_below_threshold() -> None:
    state = ProjectWizardState(free_disk_bytes=DISK_SPACE_MIN_BYTES - 1)
    msg = disk_space_pre_flight_message(state)
    assert msg is not None and "Insufficient disk space" in msg


# ---------------------------------------------------------------------------
# ProjectWizardState -- lims_project_name field
# ---------------------------------------------------------------------------


def test_state_lims_project_name_defaults_empty_and_is_settable() -> None:
    state = ProjectWizardState()
    assert state.lims_project_name == ""
    state.lims_project_name = "Microscopy Q2"
    assert state.lims_project_name == "Microscopy Q2"


# ---------------------------------------------------------------------------
# render_project_wizard -- new templates / equipment_ids kwargs
# ---------------------------------------------------------------------------


def test_render_project_wizard_with_choices_does_not_raise() -> None:
    out = render_project_wizard(
        state=ProjectWizardState(),
        templates=["lab-default", "microscopy"],
        equipment_ids=["CONFOCAL_01", "EM_02"],
        on_submit=lambda _s: None,
    )
    assert out is not None


def test_render_project_wizard_with_empty_choices_does_not_raise() -> None:
    out = render_project_wizard(
        state=ProjectWizardState(),
        templates=[],
        equipment_ids=[],
    )
    assert out is not None


def test_render_project_wizard_defaults_state_when_omitted() -> None:
    out = render_project_wizard(templates=["t"], equipment_ids=["e"])
    assert out is not None


def test_wizard_project_step_titles_cover_every_step() -> None:
    assert set(wizard_project.PROJECT_STEP_TITLES) == set(
        wizard_project.PROJECT_WIZARD_STEPS
    )
