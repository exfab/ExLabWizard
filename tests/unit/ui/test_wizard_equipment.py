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


def test_wizard_has_three_steps_with_sync_mode_hidden() -> None:
    """The sync-mode step is hidden (orchestrator/staging hidden — see
    docs/superpowers/specs/2026-05-29-hide-orchestrator-staging-design.md), so the
    wizard is identity → paths → review. The completeness-signal step was
    already removed by the quiescence redesign."""
    assert EQUIPMENT_WIZARD_STEPS == ("identity", "paths", "review")
    assert set(EQUIPMENT_STEP_TITLES) == set(EQUIPMENT_WIZARD_STEPS)
    assert "signal" not in EQUIPMENT_WIZARD_STEPS
    assert "sync_mode" not in EQUIPMENT_WIZARD_STEPS


def test_assembled_equipment_defaults_to_nas_sync_mode() -> None:
    """With the sync-mode step hidden, every wizard-built equipment is nas-mode."""
    from exlab_wizard.constants import SyncMode

    s = EquipmentWizardState()  # operator never picks a mode
    s.equipment_id = "FLOW_99"
    s.label = "Flow Cytometer 99"
    s.nas_root = "//nas01/lab"
    eq = assemble_equipment_config(s)
    assert eq.sync_mode == SyncMode.NAS


def _state_filled_for(step: str) -> EquipmentWizardState:
    s = EquipmentWizardState(active_step=step)
    s.equipment_id = "FLOW_99"
    s.label = "Flow Cytometer 99"
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


def test_can_advance_sync_mode_stage_needs_no_per_equipment_fields() -> None:
    # rclone.conf migration (Phase 8): stage-mode collects no per-equipment
    # transport in the wizard either -- the staging hop is the
    # ``orchestrator.staging_remote`` -- so picking the mode is enough.
    s = _state_filled_for("sync_mode")
    s.sync_mode = "stage"
    assert can_advance(s) is True


def test_assemble_round_trips_to_valid_equipment_config_nas() -> None:
    s = _state_filled_for("review")
    eq = assemble_equipment_config(s)
    assert eq.id == "FLOW_99"
    assert eq.sync_mode.value == "nas"
    # rclone.conf migration: nas-mode carries no per-equipment transport.
    assert not hasattr(eq, "transport")


def test_assemble_round_trips_to_valid_equipment_config_stage() -> None:
    s = _state_filled_for("review")
    s.sync_mode = "stage"
    eq = assemble_equipment_config(s)
    assert eq.sync_mode.value == "stage"
    # rclone.conf migration (Phase 8): stage-mode carries no per-equipment
    # transport -- the staging hop is the ``orchestrator.staging_remote``.
    assert not hasattr(eq, "transport")
    assert not hasattr(eq, "orchestrator_staging_transport")


def test_assemble_rejects_invalid_input() -> None:
    s = _state_filled_for("review")
    s.equipment_id = "flow-99"  # invalid
    with pytest.raises(ValidationError):
        assemble_equipment_config(s)
