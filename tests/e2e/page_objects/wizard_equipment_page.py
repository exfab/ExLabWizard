"""Page object for the Add-Equipment wizard (Redesign §6)."""

from __future__ import annotations

from typing import Any


class WizardEquipmentPage:
    def __init__(self, page: Any) -> None:
        self._page = page

    # Identity step
    @property
    def step_identity(self) -> Any:
        return self._page.locator('[data-testid="wizard-equipment-step-identity"]')

    @property
    def equipment_id(self) -> Any:
        return self._page.locator('[data-testid="wizard-equipment-id"]')

    @property
    def label(self) -> Any:
        return self._page.locator('[data-testid="wizard-equipment-label"]')

    @property
    def step_paths(self) -> Any:
        return self._page.locator('[data-testid="wizard-equipment-step-paths"]')

    # Paths -- the equipment's data dir is derived from the single app root, so
    # the wizard's paths step now collects only the NAS root.
    @property
    def nas_root(self) -> Any:
        return self._page.locator('[data-testid="wizard-equipment-nas-root"]')

    # Sync mode -- rclone.conf migration (Phase 8) removed the per-equipment
    # SFTP/SMB transport fieldsets; the radio selects nas/stage only and the
    # connection is the single nas: remote / orchestrator.staging_remote.
    @property
    def sync_mode(self) -> Any:
        return self._page.locator('[data-testid="wizard-equipment-sync-mode"]')

    @property
    def nas_note(self) -> Any:
        return self._page.locator('[data-testid="wizard-equipment-nas-note"]')

    @property
    def stage_note(self) -> Any:
        return self._page.locator('[data-testid="wizard-equipment-stage-note"]')

    # Review / confirm
    @property
    def confirm(self) -> Any:
        return self._page.locator('[data-testid="wizard-equipment-confirm"]')

    @property
    def cancel(self) -> Any:
        return self._page.locator('[data-testid="wizard-equipment-cancel"]')

    @property
    def next_button(self) -> Any:
        return self._page.locator('[data-testid="wizard-equipment-next"]')

    @property
    def back(self) -> Any:
        return self._page.locator('[data-testid="wizard-equipment-back"]')

    @property
    def success(self) -> Any:
        return self._page.locator('[data-testid="wizard-equipment-success"]')
