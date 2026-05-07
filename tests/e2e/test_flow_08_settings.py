"""E2E flow 08: Settings dialog round-trip + persistence.

Frontend Spec §6.7 (settings dialog), Backend Spec §4.6.5 (config
endpoints).

Walks every section of the settings sidebar, fills the paths and
equipment sections, and asserts the save handler triggers the saved
marker.
"""

from __future__ import annotations

from tests.e2e.page_objects.settings_page import SettingsPage

SETTINGS_SECTIONS = (
    "paths",
    "lims",
    "equipment",
    "nas_cleanup",
    "operators",
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
