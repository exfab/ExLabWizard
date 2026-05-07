"""E2E flow 02: Project wizard 7-step happy path.

Frontend Spec §6.3 (project wizard stepper).

Walks the seven-step wizard end-to-end and asserts the success card
renders after submit.
"""

from __future__ import annotations

from tests.e2e.page_objects.wizard_project_page import WizardProjectPage


def test_flow_02_project_wizard(page, server_url) -> None:
    wizard = WizardProjectPage(page)
    page.goto(f"{server_url}/wizard/project")
    page.wait_for_load_state("networkidle")

    wizard.card.wait_for(state="visible", timeout=10_000)
    wizard.stepper.wait_for(state="visible", timeout=5_000)

    # Walk every step Next, then Create on the confirm step.
    for step_id in (
        "lims_project",
        "template",
        "equipment",
        "variables",
        "readme",
        "preview",
    ):
        # Each step's container must exist.
        wizard.step(step_id).wait_for(state="attached", timeout=2_000)
        # The Next button on the active step is the visible one.
        page.locator(
            f'[data-testid="wizard-step-{step_id}"] [data-testid="wizard-next"]',
        ).first.click()

    # Confirm step: hit Create.
    page.locator(
        '[data-testid="wizard-step-confirm"] [data-testid="wizard-submit"]',
    ).first.click()
    wizard.success_card.wait_for(state="visible", timeout=5_000)
