"""Page object for the Problems tab. Frontend Spec §6.6."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Locator, Page


class ProblemsPage:
    """Locators for the Problems table + override dialog."""

    def __init__(self, page: Page) -> None:
        self._page = page

    @property
    def table(self) -> Locator:
        """The Problems table container."""
        return self._page.get_by_test_id("problems-table")

    @property
    def empty_state(self) -> Locator:
        """The empty-state label shown when no findings match."""
        return self._page.get_by_test_id("problems-empty")

    def row(self, idx: int) -> Locator:
        """The Nth visible findings row."""
        return self._page.get_by_test_id(f"problems-row-{idx}")

    def row_state(self, idx: int) -> Locator:
        """The state label inside the Nth row (Active / Override active / ...)."""
        return self._page.get_by_test_id(f"problems-row-{idx}-state")

    def row_override(self, idx: int) -> Locator:
        """The Override-and-allow-sync button on the Nth row."""
        return self._page.get_by_test_id(f"problems-row-{idx}-override")

    def row_revoke(self, idx: int) -> Locator:
        """The Revoke override button on the Nth row."""
        return self._page.get_by_test_id(f"problems-row-{idx}-revoke")
