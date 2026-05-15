"""E2E flow 18: relay-receive — received equipment auto-discovery (Redesign §3.3).

A run pushed by another device into this orchestrator's staging_root
surfaces as a received-equipment node carrying the ``relay`` visual
cue. Selecting it renders the relay metadata in the right pane.
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


def test_flow_18_received_equipment_node_appears(page, server_url) -> None:
    _goto(page, f"{server_url}/main?view=explorer")
    node = page.locator('[data-testid="tree-node-received_equipment"]')
    node.wait_for(state="visible", timeout=10_000)
    assert "RELAY_EQX" in node.inner_text()


def test_flow_18_received_metadata_shows_relay_badge(page, server_url) -> None:
    _goto(page, f"{server_url}/main?view=explorer")
    page.locator('[data-testid="tree-node-received_equipment"]').wait_for(
        state="visible", timeout=10_000
    )
    page.locator('[data-testid="tree-node-received_equipment"]').click()
    page.wait_for_load_state("networkidle")
    # The metadata-pane's relay badge is the receiver's visible cue.
    page.locator('[data-testid="metadata-pane"]').wait_for(state="visible", timeout=5_000)
    page.locator('[data-testid="metadata-relay-badge"]').wait_for(
        state="visible", timeout=5_000
    )
