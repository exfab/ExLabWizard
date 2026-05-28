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

    # Paths
    @property
    def local_root(self) -> Any:
        return self._page.locator('[data-testid="wizard-equipment-local-root"]')

    @property
    def nas_root(self) -> Any:
        return self._page.locator('[data-testid="wizard-equipment-nas-root"]')

    # Sync mode
    @property
    def sync_mode(self) -> Any:
        return self._page.locator('[data-testid="wizard-equipment-sync-mode"]')

    @property
    def transport_type(self) -> Any:
        return self._page.locator('[data-testid="wizard-equipment-transport-type"]')

    # SFTP transport fields (rclone_sftp)
    @property
    def sftp_host(self) -> Any:
        return self._page.locator('[data-testid="wizard-equipment-sftp-host"]')

    @property
    def sftp_user(self) -> Any:
        return self._page.locator('[data-testid="wizard-equipment-sftp-user"]')

    @property
    def sftp_remote_path(self) -> Any:
        return self._page.locator('[data-testid="wizard-equipment-sftp-remote-path"]')

    # SMB transport fields (rclone_smb)
    @property
    def smb_host(self) -> Any:
        return self._page.locator('[data-testid="wizard-equipment-smb-host"]')

    @property
    def smb_share(self) -> Any:
        return self._page.locator('[data-testid="wizard-equipment-smb-share"]')

    @property
    def smb_user(self) -> Any:
        return self._page.locator('[data-testid="wizard-equipment-smb-user"]')

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
