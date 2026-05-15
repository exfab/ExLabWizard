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

    # Signal
    @property
    def signal(self) -> Any:
        return self._page.locator('[data-testid="wizard-equipment-signal"]')

    @property
    def sentinel_filename(self) -> Any:
        return self._page.locator('[data-testid="wizard-equipment-sentinel-filename"]')

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
    def success(self) -> Any:
        return self._page.locator('[data-testid="wizard-equipment-success"]')
