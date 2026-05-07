"""Page object for the New Project Wizard. Frontend Spec §6.3."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Locator, Page


class WizardProjectPage:
    """Locators for the new-project wizard's seven-step stepper."""

    def __init__(self, page: Page) -> None:
        self._page = page

    @property
    def card(self) -> Locator:
        """The wizard's outermost card element."""
        return self._page.get_by_test_id("wizard-project-card")

    @property
    def stepper(self) -> Locator:
        """The vertical stepper that holds the seven steps."""
        return self._page.get_by_test_id("wizard-project-stepper")

    def step(self, step_id: str) -> Locator:
        """Locator for a specific step container."""
        return self._page.get_by_test_id(f"wizard-step-{step_id}")

    @property
    def back(self) -> Locator:
        """The back button on the active stepper navigation."""
        return self._page.get_by_test_id("wizard-back").first

    @property
    def next(self) -> Locator:
        """The primary advance button on the active stepper navigation."""
        return self._page.get_by_test_id("wizard-next").first

    @property
    def submit(self) -> Locator:
        """The Create button on the confirm step."""
        return self._page.get_by_test_id("wizard-submit").first

    @property
    def success_card(self) -> Locator:
        """The success indicator rendered after submit."""
        return self._page.get_by_test_id("wizard-project-success")
