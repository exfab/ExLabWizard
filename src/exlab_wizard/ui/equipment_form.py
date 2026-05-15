"""Shared assembler for an :class:`EquipmentConfig` from raw form fields.

GUI/Orchestrator Redesign §6: both the Settings → Equipment List section
and the new Add-Equipment wizard build their final ``EquipmentConfig``
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
    RcloneTransport,
    RsyncSshTransport,
)
from exlab_wizard.constants import (
    CompletenessSignal,
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
    completeness_signal: str,
    sentinel_filename: str,
    manifest_filename: str,
    sync_mode: str = "nas",
    # NAS transport fields (when sync_mode == "nas")
    transport_type: str = "rclone",
    rclone_remote: str = "",
    rclone_remote_path: str = "",
    ssh_target: str = "",
    ssh_key_path: str = "",
    rsync_remote_path: str = "",
    # Stage transport fields (when sync_mode == "stage")
    staging_transport_type: str = "smb_mount",
    staging_mount_point: str = "",
    staging_subpath: str = "",
) -> EquipmentConfig:
    """Assemble a validated :class:`EquipmentConfig` from raw form fields.

    Redesign §3.2: ``sync_mode`` ("nas" or "stage") dictates which
    transport sub-block is populated. ``nas`` requires the NAS
    ``transport`` block (rclone or rsync_ssh); ``stage`` requires the
    ``orchestrator_staging_transport`` block (smb_mount or file_transfer).
    Pydantic validation enforces the exclusivity rule.
    """
    mode = SyncMode(sync_mode)
    signal = CompletenessSignal(completeness_signal)

    transport: RcloneTransport | RsyncSshTransport | None = None
    orch_staging: OrchestratorStagingTransport | None = None

    if mode is SyncMode.NAS:
        if transport_type == "rsync_ssh":
            transport = RsyncSshTransport(
                type="rsync_ssh",
                ssh_target=ssh_target.strip(),
                ssh_key_path=ssh_key_path.strip() or "~/.ssh/id_ed25519",
                remote_path=rsync_remote_path.strip(),
            )
        else:
            transport = RcloneTransport(
                type="rclone",
                rclone_remote=rclone_remote.strip(),
                rclone_remote_path=rclone_remote_path.strip(),
            )
    else:  # SyncMode.STAGE
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
        completeness_signal=signal,
        sentinel_filename=(
            sentinel_filename.strip() or None
            if signal is CompletenessSignal.SENTINEL_FILE
            else None
        ),
        manifest_filename=(
            manifest_filename.strip() or None if signal is CompletenessSignal.MANIFEST else None
        ),
        sync_mode=mode,
        transport=transport,
        orchestrator_staging_transport=orch_staging,
    )
