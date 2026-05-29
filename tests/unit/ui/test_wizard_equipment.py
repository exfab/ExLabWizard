"""Unit tests for the Add-Equipment wizard. Redesign §6."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from exlab_wizard.ui.pages.wizard_equipment import (
    EQUIPMENT_STEP_TITLES,
    EQUIPMENT_WIZARD_STEPS,
    EquipmentWizardState,
    assemble_equipment_config,
    can_advance,
)


def test_wizard_has_four_steps_without_signal_step() -> None:
    """The completeness-signal step is removed by the quiescence redesign."""
    assert EQUIPMENT_WIZARD_STEPS == ("identity", "paths", "sync_mode", "review")
    assert set(EQUIPMENT_STEP_TITLES) == set(EQUIPMENT_WIZARD_STEPS)
    assert "signal" not in EQUIPMENT_WIZARD_STEPS


def _state_filled_for(step: str) -> EquipmentWizardState:
    s = EquipmentWizardState(active_step=step)
    s.equipment_id = "FLOW_99"
    s.label = "Flow Cytometer 99"
    s.local_root = "/data/lab"
    s.nas_root = "//nas01/lab"
    s.sync_mode = "nas"
    return s


def test_can_advance_identity_requires_id_and_label() -> None:
    s = EquipmentWizardState(active_step="identity")
    assert can_advance(s) is False
    s.equipment_id = "FLOW_99"
    assert can_advance(s) is False
    s.label = "Flow Cytometer 99"
    assert can_advance(s) is True


def test_can_advance_identity_rejects_invalid_id() -> None:
    s = EquipmentWizardState(active_step="identity")
    s.equipment_id = "flow-99"  # lowercase + hyphen
    s.label = "Flow Cytometer 99"
    assert can_advance(s) is False


def test_can_advance_paths_requires_both_roots() -> None:
    s = _state_filled_for("paths")
    assert can_advance(s) is True
    s.nas_root = ""
    assert can_advance(s) is False


def test_can_advance_sync_mode_nas_needs_no_transport_fields() -> None:
    # rclone.conf migration: nas-mode collects no per-equipment transport
    # in the wizard, so picking the mode is enough to advance.
    s = _state_filled_for("sync_mode")
    assert can_advance(s) is True


def test_can_advance_sync_mode_stage_requires_staging_fields() -> None:
    s = _state_filled_for("sync_mode")
    s.sync_mode = "stage"
    assert can_advance(s) is False
    s.staging_mount_point = "/mnt/staging"
    s.staging_subpath = "in/FLOW_99"
    assert can_advance(s) is True


def test_assemble_round_trips_to_valid_equipment_config_nas() -> None:
    s = _state_filled_for("review")
    eq = assemble_equipment_config(s)
    assert eq.id == "FLOW_99"
    assert eq.sync_mode.value == "nas"
    # rclone.conf migration: nas-mode carries no per-equipment transport.
    assert eq.transport is None


def test_assemble_round_trips_to_valid_equipment_config_stage() -> None:
    s = _state_filled_for("review")
    s.sync_mode = "stage"
    s.staging_mount_point = "/mnt/staging"
    s.staging_subpath = "in/FLOW_99"
    eq = assemble_equipment_config(s)
    assert eq.sync_mode.value == "stage"
    assert eq.transport is None
    assert eq.orchestrator_staging_transport is not None


def test_assemble_rejects_invalid_input() -> None:
    s = _state_filled_for("review")
    s.equipment_id = "flow-99"  # invalid
    with pytest.raises(ValidationError):
        assemble_equipment_config(s)
