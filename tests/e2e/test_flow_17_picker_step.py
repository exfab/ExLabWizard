"""E2E flow 17: creation-button enablement on received nodes (Redesign decision 1).

When a received-equipment node is selected in the tree, the three
creation buttons (New Project / New Run / New Test Run) MUST be
disabled. Owned-equipment / project / run selections keep them
enabled.
"""

from __future__ import annotations


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


def test_flow_17_creation_buttons_enabled_on_owned_node(page, server_url) -> None:
    _goto(page, f"{server_url}/main?view=explorer")
    page.locator('[data-testid="tree-node-equipment"]').first.wait_for(
        state="visible", timeout=10_000
    )
    # Click the owned equipment node.
    page.locator('[data-testid="tree-node-equipment"]').first.click()
    page.wait_for_load_state("networkidle")
    # All three creation buttons remain enabled (no 'disable' prop).
    for testid in ("toolbar-new-project", "toolbar-new-run", "toolbar-new-test-run"):
        btn = page.locator(f'[data-testid="{testid}"]')
        btn.wait_for(state="visible", timeout=5_000)
        # NiceGUI's Quasar button renders disabled as aria-disabled=true.
        assert btn.get_attribute("aria-disabled") != "true"


def test_flow_17_creation_buttons_disabled_on_received_node(page, server_url) -> None:
    _goto(page, f"{server_url}/main?view=explorer")
    page.locator('[data-testid="tree-node-received_equipment"]').wait_for(
        state="visible", timeout=10_000
    )
    page.locator('[data-testid="tree-node-received_equipment"]').click()
    page.wait_for_load_state("networkidle")
    for testid in ("toolbar-new-project", "toolbar-new-run", "toolbar-new-test-run"):
        btn = page.locator(f'[data-testid="{testid}"]')
        btn.wait_for(state="visible", timeout=5_000)
        assert btn.get_attribute("aria-disabled") == "true", (
            f"{testid} should be disabled when a received-equipment node is selected"
        )
