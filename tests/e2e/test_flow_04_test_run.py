"""E2E flow 04: Test-run wizard end-to-end.

Frontend Spec §6.4 (run wizard, test mode), Backend Spec §7.6 (test
run session machine).

The test-run wizard's mode flag is bound at construction; navigating
back to step 1 must NOT swap the mode (the spec's "no mid-session
mode change" invariant). Closing and reopening the wizard is the only
way to swap.
"""

from __future__ import annotations

from tests.e2e.page_objects.wizard_run_page import WizardRunPage


def test_flow_04_test_run(page, server_url) -> None:
    wizard = WizardRunPage(page)
    page.goto(f"{server_url}/wizard/test-run")
    page.wait_for_load_state("networkidle")

    wizard.stepper.wait_for(state="visible", timeout=10_000)
    # The test mode badge is present and remains the only mode badge,
    # confirming the wizard's run_kind is bound at construction.
    assert wizard.mode_badge_test.count() == 1
    assert wizard.mode_badge_experimental.count() == 0

    # Walk forward two steps...
    for step_id in ("project_equipment", "template"):
        wizard.step(step_id).wait_for(state="attached", timeout=2_000)
        page.locator(
            f'[data-testid="wizard-run-step-{step_id}"] [data-testid="wizard-run-next"]',
        ).first.click()

    # ...then walk back to step 1 and verify the badge is still TEST.
    page.locator(
        '[data-testid="wizard-run-step-variables"] [data-testid="wizard-run-back"]',
    ).first.click()
    page.locator(
        '[data-testid="wizard-run-step-template"] [data-testid="wizard-run-back"]',
    ).first.click()
    assert wizard.mode_badge_test.count() == 1
    assert wizard.mode_badge_experimental.count() == 0

    # Walk to confirm and submit.
    for step_id in (
        "project_equipment",
        "template",
        "variables",
        "readme",
        "preview",
    ):
        page.locator(
            f'[data-testid="wizard-run-step-{step_id}"] [data-testid="wizard-run-next"]',
        ).first.click()
    page.locator(
        '[data-testid="wizard-run-step-confirm"] [data-testid="wizard-run-submit"]',
    ).first.click()
    wizard.success_card.wait_for(state="visible", timeout=5_000)
