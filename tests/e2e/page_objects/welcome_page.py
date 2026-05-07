"""Page object for the first-launch welcome card. Frontend Spec §6.1."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Locator, Page


class WelcomePage:
    """Page object for the welcome card shown on first launch. Frontend Spec §6.1."""

    def __init__(self, page: Page) -> None:
        self._page = page

    @property
    def card(self) -> Locator:
        """The welcome card container."""
        return self._page.get_by_test_id("welcome-card")

    @property
    def headline(self) -> Locator:
        """The welcome card headline / title element."""
        return self._page.get_by_test_id("welcome-headline")

    @property
    def autostart_toggle(self) -> Locator:
        """The autostart-on-login toggle on the welcome card."""
        return self._page.get_by_test_id("welcome-autostart-toggle")

    @property
    def get_started(self) -> Locator:
        """The primary "Get started" CTA on the welcome card."""
        return self._page.get_by_test_id("welcome-get-started")

    @property
    def skip_for_now(self) -> Locator:
        """The secondary "Skip for now" CTA on the welcome card."""
        return self._page.get_by_test_id("welcome-skip-for-now")

    @property
    def status_marker(self) -> Locator:
        """Hidden marker emitted by the test app to surface state."""
        return self._page.get_by_test_id("welcome-status")
