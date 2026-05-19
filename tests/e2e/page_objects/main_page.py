"""Page object for the main window. Frontend Spec §6.

Selectors target ``data-testid`` attributes added to the Phase 12
NiceGUI components in the Phase 16 follow-up.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Locator, Page


class MainPage:
    """Page object for the main window. Frontend Spec §6.1, §6.2."""

    def __init__(self, page: Page) -> None:
        self._page = page

    @property
    def tree(self) -> Locator:
        """The left-pane project / session tree (§6.2.1)."""
        return self._page.get_by_test_id("main-tree")

    @property
    def setup_incomplete_banner(self) -> Locator:
        """Banner shown while the setup gate is open (§6.2.4)."""
        return self._page.get_by_test_id("setup-incomplete-banner")

    @property
    def toolbar_new_project(self) -> Locator:
        """Toolbar button that opens the new-project wizard (§6.2.2)."""
        return self._page.get_by_test_id("toolbar-new-project")

    @property
    def toolbar_new_run(self) -> Locator:
        """Toolbar button that opens the new-run wizard (§6.2.2)."""
        return self._page.get_by_test_id("toolbar-new-run")

    @property
    def toolbar_new_test_run(self) -> Locator:
        """Toolbar button that opens the new-test-run wizard (§6.2.2)."""
        return self._page.get_by_test_id("toolbar-new-test-run")

    @property
    def toolbar_settings(self) -> Locator:
        """Toolbar button that opens the settings dialog (§6.2.2)."""
        return self._page.get_by_test_id("toolbar-settings")

    @property
    def toolbar_refresh(self) -> Locator:
        """Toolbar refresh button (§6.2.2)."""
        return self._page.get_by_test_id("toolbar-refresh")

    @property
    def toolbar_add_equipment(self) -> Locator:
        """Toolbar Add-Equipment button (Redesign §4 / §6)."""
        return self._page.get_by_test_id("toolbar-add-equipment")

    @property
    def search_box(self) -> Locator:
        """The tree search input (§6.2.1)."""
        return self._page.get_by_test_id("main-search")

    @property
    def tab_metadata(self) -> Locator:
        """Metadata tab (Redesign §4.4)."""
        return self._page.get_by_test_id("tab-metadata")

    @property
    def tab_problems(self) -> Locator:
        """Problems tab (§6.2.3 / Redesign §4.4)."""
        return self._page.get_by_test_id("tab-problems")

    @property
    def toggle_right_pane(self) -> Locator:
        """Right-pane collapse toggle (Redesign §4)."""
        return self._page.get_by_test_id("toggle-right-pane")

    @property
    def footer_clear_verified(self) -> Locator:
        """Footer bulk Clear-verified button (Redesign §4.6)."""
        return self._page.get_by_test_id("footer-clear-verified")

    @property
    def footer_staging_segment(self) -> Locator:
        """Footer Staging status segment (Redesign §4.6)."""
        return self._page.get_by_test_id("footer-staging-segment")
