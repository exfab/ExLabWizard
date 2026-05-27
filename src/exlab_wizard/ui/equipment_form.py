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
    RcloneSftpTransport,
    RcloneSmbTransport,
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
    # NAS transport fields (when sync_mode == "nas").
    transport_type: str = "rclone_sftp",
    # rclone_sftp fields:
    sftp_host: str = "",
    sftp_port: int = 22,
    sftp_user: str = "",
    sftp_remote_path: str = "",
    # rclone_smb fields:
    smb_host: str = "",
    smb_share: str = "",
    smb_user: str = "",
    smb_domain: str = "",
    smb_remote_path: str = "",
    # Stage transport fields (when sync_mode == "stage").
    staging_transport_type: str = "smb_mount",
    staging_mount_point: str = "",
    staging_subpath: str = "",
) -> EquipmentConfig:
    """Assemble a validated :class:`EquipmentConfig` from raw form fields.

    Redesign §3.2: ``sync_mode`` ("nas" or "stage") dictates which
    transport sub-block is populated. ``nas`` requires the NAS
    ``transport`` block (rclone_sftp or rclone_smb, both password-based);
    ``stage`` requires the ``orchestrator_staging_transport`` block
    (smb_mount or file_transfer). Pydantic validation enforces the
    exclusivity rule.
    """
    mode = SyncMode(sync_mode)

    transport: RcloneSftpTransport | RcloneSmbTransport | None = None
    orch_staging: OrchestratorStagingTransport | None = None

    if mode is SyncMode.NAS:
        if transport_type == "rclone_smb":
            transport = RcloneSmbTransport(
                type="rclone_smb",
                host=smb_host.strip(),
                share=smb_share.strip(),
                user=smb_user.strip(),
                domain=smb_domain.strip(),
                remote_path=smb_remote_path.strip(),
            )
        else:
            transport = RcloneSftpTransport(
                type="rclone_sftp",
                host=sftp_host.strip(),
                port=int(sftp_port),
                user=sftp_user.strip(),
                remote_path=sftp_remote_path.strip(),
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
        sync_mode=mode,
        transport=transport,
        orchestrator_staging_transport=orch_staging,
    )
