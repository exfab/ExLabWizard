"""Page object for the New Run Wizard. Frontend Spec §6.4."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Locator, Page


class WizardRunPage:
    """Locators for the run wizard (experimental + test mode)."""

    def __init__(self, page: Page) -> None:
        self._page = page

    @property
    def stepper(self) -> Locator:
        """The vertical stepper that holds the six steps."""
        return self._page.get_by_test_id("wizard-run-stepper")

    @property
    def title(self) -> Locator:
        """The wizard's title label (changes per run kind)."""
        return self._page.get_by_test_id("wizard-run-title")

    @property
    def mode_badge_experimental(self) -> Locator:
        """The mode badge for experimental runs."""
        return self._page.get_by_test_id("mode-badge-experimental")

    @property
    def mode_badge_test(self) -> Locator:
        """The mode badge for test runs."""
        return self._page.get_by_test_id("mode-badge-test")

    def step(self, step_id: str) -> Locator:
        """Locator for a specific step container."""
        return self._page.get_by_test_id(f"wizard-run-step-{step_id}")

    @property
    def back(self) -> Locator:
        """The back button on the active stepper navigation.

        Absent on the first step -- that step exits via ``cancel``.
        """
        return self._page.get_by_test_id("wizard-run-back").first

    @property
    def cancel(self) -> Locator:
        """The cancel button; present on every step, returns to /main."""
        return self._page.get_by_test_id("wizard-run-cancel").first

    @property
    def next(self) -> Locator:
        """The primary advance button on the active stepper navigation."""
        return self._page.get_by_test_id("wizard-run-next").first

    @property
    def submit(self) -> Locator:
        """The Create button on the confirm step."""
        return self._page.get_by_test_id("wizard-run-submit").first

    @property
    def success_card(self) -> Locator:
        """The success indicator rendered after submit."""
        return self._page.get_by_test_id("wizard-run-success")
