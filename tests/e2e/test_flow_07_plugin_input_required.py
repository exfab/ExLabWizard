"""E2E flow 07: Plugin input-required round trip.

Frontend Spec §6.4.5 (plugin step), Backend Spec §6.4
(``WAIT_PLUGIN_INPUT`` state + reply contract).

Drives the plugin-input dialog: navigate to the plugin-input route,
fill the requested field, click Submit, and assert the wizard resumes
on the project wizard with the provided value visible in the URL.
"""

from __future__ import annotations


def test_flow_07_plugin_input_required(page, server_url) -> None:
    page.goto(f"{server_url}/plugin-input")
    page.wait_for_load_state("networkidle")

    page.locator('[data-testid="plugin-input-dialog"]').wait_for(state="visible", timeout=10_000)
    page.locator('[data-testid="plugin-input-headline"]').wait_for(state="visible")
    page.locator('[data-testid="plugin-input-field-operator_initials"]').fill("AS")
    page.locator('[data-testid="plugin-input-submit"]').click()

    # The submit handler issues a NiceGUI ``ui.navigate.to`` over the
    # WebSocket; the round-trip can outlast a ``networkidle`` window on
    # slower CI runners, so wait for the resumed-wizard URL explicitly.
    page.wait_for_url("**/wizard/project*", timeout=10_000)
    assert "resumed=1" in page.url
    assert "v=AS" in page.url
    page.locator('[data-testid="wizard-project-card"]').wait_for(state="visible", timeout=10_000)
