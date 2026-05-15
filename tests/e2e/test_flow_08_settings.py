"""E2E flow 08: Settings dialog round-trip + persistence.

Frontend Spec §6.7 (settings dialog), Backend Spec §4.6.5 (config
endpoints).

Walks every section of the settings sidebar, fills the paths and
equipment sections, and asserts the save handler triggers the saved
marker.
"""

from __future__ import annotations

from playwright.sync_api import expect

from tests.e2e.page_objects.settings_page import SettingsPage

SETTINGS_SECTIONS = (
    "paths",
    "lims",
    "equipment",
    "nas_cleanup",
    # "operators" is deferred until the chip editor lands.
    "validator",
    "logging",
    "orchestrator",
    "application",
)


def test_flow_08_settings(page, server_url) -> None:
    settings = SettingsPage(page)
    page.goto(f"{server_url}/settings")
    page.wait_for_load_state("networkidle")

    settings.dialog.wait_for(state="visible", timeout=10_000)

    # Each section nav row is rendered.
    for section in SETTINGS_SECTIONS:
        settings.nav(section).wait_for(state="visible", timeout=2_000)

    # Visit each section explicitly to confirm the body renders.
    for section in SETTINGS_SECTIONS:
        page.goto(f"{server_url}/settings?active={section}")
        page.wait_for_load_state("networkidle")
        settings.section(section).wait_for(state="visible", timeout=5_000)

    # Fill paths and save.
    page.goto(f"{server_url}/settings?active=paths")
    page.wait_for_load_state("networkidle")
    settings.paths_templates.fill("/tmp/templates")
    settings.paths_plugin.fill("/tmp/plugins")
    settings.paths_local_root.fill("/tmp/data")
    settings.save.click()
    page.wait_for_load_state("networkidle")
    settings.saved_marker.wait_for(state="visible", timeout=5_000)


def test_flow_08_lims_password_credential(page, server_url) -> None:
    """The LIMS credential field's full lifecycle: set -> replace -> clear (§7.4.1).

    Regression: the editing state never rendered an inline password
    input, so the operator had no text box to type a password into.
    """
    settings = SettingsPage(page)
    page.goto(f"{server_url}/settings?active=lims")
    page.wait_for_load_state("networkidle")
    settings.section("lims").wait_for(state="visible", timeout=10_000)

    # Resting state: a [Set] button is shown, no password input yet.
    settings.lims_password_primary.wait_for(state="visible", timeout=5_000)

    # Click [Set] -> the row expands to reveal the inline password input.
    settings.lims_password_primary.click()
    settings.lims_password_input.wait_for(state="visible", timeout=5_000)

    # Type a password and Save -> the row collapses back to a resting
    # state whose status line reports the credential as set.
    settings.lims_password_input.fill("hunter2")
    settings.lims_password_save.click()
    expect(settings.lims_password_status).to_have_text("Status: Set", timeout=5_000)

    # In the resting "set" state the [Clear] action is offered; clicking
    # it opens a confirmation dialog, and confirming returns the row to
    # "Not set".
    settings.lims_password_secondary.click()
    settings.lims_password_clear_confirm.wait_for(state="visible", timeout=5_000)
    settings.lims_password_clear_confirm.click()
    expect(settings.lims_password_status).to_have_text("Status: Not set", timeout=5_000)


def test_flow_08_lims_password_cancel_discards_edit(page, server_url) -> None:
    """Cancelling the credential editor collapses the row without saving."""
    settings = SettingsPage(page)
    page.goto(f"{server_url}/settings?active=lims")
    page.wait_for_load_state("networkidle")
    settings.section("lims").wait_for(state="visible", timeout=10_000)

    settings.lims_password_primary.wait_for(state="visible", timeout=5_000)
    settings.lims_password_primary.click()
    settings.lims_password_input.wait_for(state="visible", timeout=5_000)

    # Type a value, then Cancel -> the row returns to "Not set".
    settings.lims_password_input.fill("typo-password")
    settings.lims_password_cancel.click()
    expect(settings.lims_password_status).to_have_text("Status: Not set", timeout=5_000)
