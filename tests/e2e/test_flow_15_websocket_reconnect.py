"""E2E flow 15: WebSocket reconnect + degraded-mode banner.

Frontend Spec §6.14 (degraded-mode banner), Backend Spec §4.6.2 (WS
reconnect contract).

The /reconnect test route renders the banner stack with the
``reconnecting`` banner pre-activated. Asserting the banner's
``data-testid`` is present is the e2e proxy for the live reconnect
flow; the live flow's WebSocket retry timing is exercised by the
unit + integration suites.
"""

from __future__ import annotations


def test_flow_15_websocket_reconnect(page, server_url) -> None:
    page.goto(f"{server_url}/reconnect")
    page.wait_for_load_state("networkidle")

    page.locator('[data-testid="banner-reconnecting"]').wait_for(state="visible", timeout=10_000)
