"""Shared assembler for an :class:`EquipmentConfig` from raw form fields.

GUI/Orchestrator Redesign §6: both the Settings → Equipment List section
and the Add-Equipment wizard build their final ``EquipmentConfig``
through this single function so the two surfaces stay in lockstep
without copy-paste drift.

This module is pure (no NiceGUI dependency) and lives outside the
``pages/`` package so the wizard module can import it without a circular
dependency through Settings.
"""

from __future__ import annotations

from exlab_wizard.config.models import (
    EquipmentConfig,
    OrchestratorStagingTransport,
)
from exlab_wizard.constants import (
    OrchestratorTransportType,
    SyncMode,
)

__all__ = ["build_equipment_config"]


def build_equipment_config(
    *,
    equipment_id: str,
    label: str,
    local_root: str,
    nas_root: str,
    sync_mode: str = "nas",
    # Stage transport fields (when sync_mode == "stage").
    staging_transport_type: str = "smb_mount",
    staging_mount_point: str = "",
    staging_subpath: str = "",
) -> EquipmentConfig:
    """Assemble a validated :class:`EquipmentConfig` from raw form fields.

    Redesign §3.2: ``sync_mode`` ("nas" or "stage") dictates the device's
    role. rclone.conf NAS-sync migration: nas-mode no longer carries a
    per-equipment SFTP/SMB transport -- the connection is defined once by
    the ``nas:`` remote -- so the nas-mode build path leaves
    ``transport=None``. ``stage`` still requires the
    ``orchestrator_staging_transport`` block (smb_mount or file_transfer);
    Pydantic validation enforces the per-mode rules.
    """
    mode = SyncMode(sync_mode)

    orch_staging: OrchestratorStagingTransport | None = None

    if mode is SyncMode.STAGE:
        orch_staging = OrchestratorStagingTransport(
            type=OrchestratorTransportType(staging_transport_type),
            mount_point=staging_mount_point.strip(),
            staging_subpath=staging_subpath.strip(),
        )

    return EquipmentConfig(
        id=equipment_id.strip(),
        label=label.strip(),
        local_root=local_root.strip(),
        nas_root=nas_root.strip(),
        sync_mode=mode,
        transport=None,
        orchestrator_staging_transport=orch_staging,
    )
