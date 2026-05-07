"""E2E flow 13: Keyboard shortcuts + accessibility focus management.

Frontend Spec §6.12 (keyboard map), §5 (accessibility tokens).

The /keyboard route registers a small JS listener that flips a
``data-action`` attribute on a hidden marker when Cmd/Ctrl+N or
Escape is pressed. Asserting the marker's attribute lets us drive the
shortcut keys deterministically through Playwright.
"""

from __future__ import annotations


def test_flow_13_keyboard(page, server_url) -> None:
    page.goto(f"{server_url}/keyboard")
    page.wait_for_load_state("networkidle")
    page.locator('[data-testid="keyboard-page-loaded"]').wait_for(state="visible", timeout=10_000)

    marker = page.locator('[data-testid="keyboard-marker"]')

    # Cmd/Ctrl+N -> the JS listener flips data-action="new-project".
    page.keyboard.press("Control+n")
    page.wait_for_function(
        "document.querySelector('[data-testid=\"keyboard-marker\"]')"
        ".getAttribute('data-action') === 'new-project'",
        timeout=5_000,
    )
    assert marker.get_attribute("data-action") == "new-project"

    # Esc -> data-action="escape".
    page.keyboard.press("Escape")
    page.wait_for_function(
        "document.querySelector('[data-testid=\"keyboard-marker\"]')"
        ".getAttribute('data-action') === 'escape'",
        timeout=5_000,
    )
    assert marker.get_attribute("data-action") == "escape"
