"""Page object for the Settings dialog. Frontend Spec §6.7."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Locator, Page


class SettingsPage:
    """Locators for the settings dialog's nine-section layout."""

    def __init__(self, page: Page) -> None:
        self._page = page

    @property
    def dialog(self) -> Locator:
        """The settings dialog card."""
        return self._page.get_by_test_id("settings-dialog")

    @property
    def incomplete_banner(self) -> Locator:
        """The setup-incomplete banner shown inside the dialog."""
        return self._page.get_by_test_id("settings-incomplete-banner")

    def nav(self, section: str) -> Locator:
        """The sidebar nav row for a section."""
        return self._page.get_by_test_id(f"settings-nav-{section}")

    def section(self, section: str) -> Locator:
        """The body container for a section."""
        return self._page.get_by_test_id(f"settings-section-{section}")

    @property
    def paths_templates(self) -> Locator:
        """Paths section: templates directory input."""
        return self._page.get_by_test_id("settings-paths-templates")

    @property
    def paths_plugin(self) -> Locator:
        """Paths section: plugin directory input."""
        return self._page.get_by_test_id("settings-paths-plugin")

    @property
    def paths_local_root(self) -> Locator:
        """Paths section: local data root input."""
        return self._page.get_by_test_id("settings-paths-local-root")

    @property
    def lims_password_primary(self) -> Locator:
        """LIMS section: the credential row's primary button ([Set] / [Replace])."""
        return self._page.get_by_test_id("settings-lims-password-primary")

    @property
    def lims_password_secondary(self) -> Locator:
        """LIMS section: the credential row's secondary button ([Clear])."""
        return self._page.get_by_test_id("settings-lims-password-secondary")

    @property
    def lims_password_input(self) -> Locator:
        """LIMS section: the inline password input shown while editing."""
        return self._page.get_by_test_id("settings-lims-password-input")

    @property
    def lims_password_save(self) -> Locator:
        """LIMS section: the credential row's Save button (editing state)."""
        return self._page.get_by_test_id("settings-lims-password-save")

    @property
    def lims_password_cancel(self) -> Locator:
        """LIMS section: the credential row's Cancel button (editing state)."""
        return self._page.get_by_test_id("settings-lims-password-cancel")

    @property
    def lims_password_clear_confirm(self) -> Locator:
        """LIMS section: the confirm button in the Clear-credential dialog."""
        return self._page.get_by_test_id("settings-lims-password-clear-confirm")

    @property
    def lims_password_status(self) -> Locator:
        """LIMS section: the credential row's status line (resting states)."""
        return self._page.get_by_test_id("settings-lims-password-status")

    @property
    def equipment_id(self) -> Locator:
        """Equipment section: equipment-id input."""
        return self._page.get_by_test_id("settings-equipment-id")

    @property
    def equipment_add(self) -> Locator:
        """Equipment section: add-equipment button."""
        return self._page.get_by_test_id("settings-equipment-add")

    @property
    def save(self) -> Locator:
        """Save button."""
        return self._page.get_by_test_id("settings-save")

    @property
    def discard(self) -> Locator:
        """Discard button."""
        return self._page.get_by_test_id("settings-discard")

    @property
    def saved_marker(self) -> Locator:
        """The marker shown after a successful save."""
        return self._page.get_by_test_id("settings-saved")
