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

from exlab_wizard.config.models import EquipmentConfig
from exlab_wizard.constants import SyncMode

__all__ = ["build_equipment_config"]


def build_equipment_config(
    *,
    equipment_id: str,
    label: str,
    nas_root: str,
    sync_mode: str = "nas",
) -> EquipmentConfig:
    """Assemble a validated :class:`EquipmentConfig` from raw form fields.

    Redesign §3.2: ``sync_mode`` ("nas" or "stage") dictates the device's
    role. rclone.conf NAS-sync migration (Phase 8): neither mode carries a
    per-equipment connection block. The NAS connection is defined once by the
    ``nas:`` remote; the staging hop is defined once by
    ``orchestrator.staging_remote`` / ``staging_base_root``. The push target
    is selected by ``sync_mode`` at sync time.

    Equipment no longer stores its own ``local_root``: its data directory is
    derived from the single app root (``<config.paths.local_root>/<id>``).
    """
    return EquipmentConfig(
        id=equipment_id.strip(),
        label=label.strip(),
        nas_root=nas_root.strip(),
        sync_mode=SyncMode(sync_mode),
    )
