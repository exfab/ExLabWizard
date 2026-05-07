"""Page object for the orchestrator staging dock. Frontend Spec §3.9."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Locator, Page


class StagingPage:
    """Locators for the staging dock + per-row actions."""

    def __init__(self, page: Page) -> None:
        self._page = page

    @property
    def dock(self) -> Locator:
        """The staging dock container."""
        return self._page.get_by_test_id("staging-dock")

    def row(self, idx: int) -> Locator:
        """The Nth staging row."""
        return self._page.get_by_test_id(f"staging-row-{idx}")

    def force_sync(self, idx: int) -> Locator:
        """The Force-sync button on the Nth row."""
        return self._page.get_by_test_id(f"staging-row-{idx}-force-sync")

    def clear(self, idx: int) -> Locator:
        """The Clear button on the Nth row."""
        return self._page.get_by_test_id(f"staging-row-{idx}-clear")

    def view_log(self, idx: int) -> Locator:
        """The View-log button on the Nth row."""
        return self._page.get_by_test_id(f"staging-row-{idx}-view-log")

    @property
    def clear_verified(self) -> Locator:
        """The toolbar Clear-verified-runs button."""
        return self._page.get_by_test_id("staging-clear-verified")
