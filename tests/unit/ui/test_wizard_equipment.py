"""Unit tests for the Add-Equipment wizard. Redesign §6."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from exlab_wizard.ui.pages.wizard_equipment import (
    EquipmentWizardState,
    assemble_equipment_config,
    can_advance,
)


def _state_filled_for(step: str) -> EquipmentWizardState:
    s = EquipmentWizardState(active_step=step)
    s.equipment_id = "FLOW_99"
    s.label = "Flow Cytometer 99"
    s.local_root = "/data/lab"
    s.nas_root = "//nas01/lab"
    s.sync_mode = "nas"
    s.transport_type = "rclone"
    s.rclone_remote = "lab-nas"
    s.rclone_remote_path = "lab/FLOW_99"
    s.completeness_signal = "sentinel_file"
    s.sentinel_filename = "done.flag"
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


def test_can_advance_sync_mode_nas_requires_rclone_fields() -> None:
    s = _state_filled_for("sync_mode")
    assert can_advance(s) is True
    s.rclone_remote = ""
    assert can_advance(s) is False


def test_can_advance_sync_mode_stage_requires_staging_fields() -> None:
    s = _state_filled_for("sync_mode")
    s.sync_mode = "stage"
    assert can_advance(s) is False
    s.staging_mount_point = "/mnt/staging"
    s.staging_subpath = "in/FLOW_99"
    assert can_advance(s) is True


def test_can_advance_signal_requires_matching_filename() -> None:
    s = _state_filled_for("signal")
    assert can_advance(s) is True
    s.completeness_signal = "manifest"
    s.sentinel_filename = ""
    s.manifest_filename = ""
    assert can_advance(s) is False
    s.manifest_filename = "manifest.json"
    assert can_advance(s) is True


def test_assemble_round_trips_to_valid_equipment_config_nas() -> None:
    s = _state_filled_for("review")
    eq = assemble_equipment_config(s)
    assert eq.id == "FLOW_99"
    assert eq.sync_mode.value == "nas"
    assert eq.transport is not None


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
