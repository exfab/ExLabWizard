"""Page object for the main window. Frontend Spec §6.

Selectors target ``data-testid`` attributes that the Phase 12 NiceGUI
components are expected to expose in the Phase 16 follow-up retrofit
(see ``tests/e2e/README.md``). The page object is intentionally
minimal -- enough hooks for the placeholder flows to compile and to
guide the data-testid retrofit.
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
    def toolbar_settings(self) -> Locator:
        """Toolbar button that opens the settings dialog (§6.2.2)."""
        return self._page.get_by_test_id("toolbar-settings")
