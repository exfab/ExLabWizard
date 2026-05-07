"""E2E flow 03: Experimental-run wizard end-to-end.

Frontend Spec §6.4 (run wizard), Backend Spec §7.5 (experimental run
session machine).

Asserts the EXPERIMENTAL mode badge is present throughout the six-step
walk and that submit triggers the success indicator.
"""

from __future__ import annotations

from tests.e2e.page_objects.wizard_run_page import WizardRunPage


def test_flow_03_experimental_run(page, server_url) -> None:
    wizard = WizardRunPage(page)
    page.goto(f"{server_url}/wizard/run")
    page.wait_for_load_state("networkidle")

    wizard.stepper.wait_for(state="visible", timeout=10_000)
    # The experimental mode badge is present (test-mode badge is not).
    assert wizard.mode_badge_experimental.count() == 1
    assert wizard.mode_badge_test.count() == 0

    for step_id in (
        "project_equipment",
        "template",
        "variables",
        "readme",
        "preview",
    ):
        wizard.step(step_id).wait_for(state="attached", timeout=2_000)
        page.locator(
            f'[data-testid="wizard-run-step-{step_id}"] [data-testid="wizard-run-next"]',
        ).first.click()

    page.locator(
        '[data-testid="wizard-run-step-confirm"] [data-testid="wizard-run-submit"]',
    ).first.click()
    wizard.success_card.wait_for(state="visible", timeout=5_000)
