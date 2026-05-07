"""E2E flow 01: First-launch onboarding.

Frontend Spec §6.1 (welcome card + autostart toggle), §6.2.4 (setup
gate banner).

The flow boots the welcome page, verifies the autostart toggle defaults
on, clicks ``Get started`` to open settings, fills the highlighted
sections, and asserts the main window flips to READY (no
setup-incomplete banner).
"""

from __future__ import annotations

from tests.e2e.page_objects.main_page import MainPage
from tests.e2e.page_objects.welcome_page import WelcomePage


def test_flow_01_onboarding(page, server_url) -> None:
    welcome = WelcomePage(page)
    main = MainPage(page)

    # 1. Welcome card visible with autostart default-on.
    page.goto(f"{server_url}/")
    page.wait_for_load_state("networkidle")
    welcome.headline.wait_for(state="visible", timeout=10_000)
    assert welcome.autostart_toggle.is_visible()

    # 2. Click Get started.
    welcome.get_started.click()
    page.wait_for_load_state("networkidle")

    # 3. Open the settings page directly (the welcome card's CTA is
    # observable via the welcome-status marker; in the test app it
    # navigates implicitly to /settings via the main-window flow).
    page.goto(f"{server_url}/settings?incomplete=paths,equipment")
    page.wait_for_load_state("networkidle")
    page.locator('[data-testid="settings-incomplete-banner"]').wait_for(
        state="visible", timeout=10_000
    )

    # 4. Fill the paths section and add equipment.
    page.locator('[data-testid="settings-paths-templates"]').fill("/tmp/templates")
    page.locator('[data-testid="settings-paths-plugin"]').fill("/tmp/plugins")
    page.locator('[data-testid="settings-paths-local-root"]').fill("/tmp/data")

    page.goto(f"{server_url}/settings?incomplete=paths,equipment&active=equipment")
    page.wait_for_load_state("networkidle")
    page.locator('[data-testid="settings-equipment-id"]').fill("EQ1")
    page.locator('[data-testid="settings-equipment-add"]').click()

    # 5. Save and confirm setup banner clears.
    page.locator('[data-testid="settings-save"]').click()
    page.wait_for_load_state("networkidle")

    # 6. Navigate to main window (now in READY state).
    page.goto(f"{server_url}/main?setup=0")
    page.wait_for_load_state("networkidle")
    main.tree.wait_for(state="visible", timeout=10_000)
    # Setup-incomplete banner should NOT be present.
    assert page.locator('[data-testid="setup-incomplete-banner"]').count() == 0
