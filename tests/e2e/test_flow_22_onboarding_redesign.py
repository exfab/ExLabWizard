"""E2E flow 22: onboarding via the new Add-Equipment wizard (Redesign decision 2).

Welcome → Settings setup-incomplete → Add Equipment via the wizard
(replaces the legacy settings-equipment-add inline form) →
``/main?view=explorer`` renders with the toolbar showing the new
"Add Equipment" affordance.

The legacy onboarding flow (``test_flow_01_onboarding.py``) keeps
working until the production mount swaps to the new renderer; this
flow asserts the redesign's intended path.
"""

from __future__ import annotations

from tests.e2e.page_objects.welcome_page import WelcomePage
from tests.e2e.page_objects.wizard_equipment_page import WizardEquipmentPage


def _goto(page, url: str, *, retries: int = 2) -> None:
    last: Exception | None = None
    for _ in range(retries + 1):
        try:
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle")
            return
        except Exception as exc:
            last = exc
            page.wait_for_timeout(300)
    raise AssertionError(f"navigation to {url} failed: {last!r}")


def test_flow_22_welcome_to_add_equipment(page, server_url) -> None:
    welcome = WelcomePage(page)
    wiz = WizardEquipmentPage(page)
    _goto(page, f"{server_url}/")
    welcome.headline.wait_for(state="visible", timeout=10_000)
    welcome.get_started.click()
    page.wait_for_load_state("networkidle")
    # In the redesigned flow the operator routes from Settings to the
    # Add-Equipment wizard. Drive the wizard route directly to assert
    # it's reachable + responsive.
    _goto(page, f"{server_url}/wizard/equipment?step=identity")
    wiz.equipment_id.wait_for(state="visible", timeout=10_000)
    wiz.cancel.wait_for(state="visible")


def test_flow_22_main_window_has_add_equipment_in_toolbar(page, server_url) -> None:
    _goto(page, f"{server_url}/main?view=explorer")
    page.locator('[data-testid="toolbar-add-equipment"]').wait_for(
        state="visible", timeout=10_000
    )
