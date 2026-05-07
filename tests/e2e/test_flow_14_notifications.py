"""E2E flow 14: Tray notifications + in-app toasts.

Frontend Spec §6.13 (notification surface), Backend Spec §12.4 (tray
notifications) + §11.9 (in-app problem toasts).

The notification helpers can render any of the §2.2.3 closed set of
five banner ids. The /notifications test route accepts a ``?banner=``
query param and renders the banner stack; this flow asserts each of
the five banner variants surfaces a stable ``data-testid`` selector.
"""

from __future__ import annotations

import pytest

BANNER_IDS = (
    "setup_incomplete",
    "sync_blocked_on_success_card",
    "lims_unreachable",
    "nas_unreachable",
    "reconnecting",
)


@pytest.mark.parametrize("banner_id", BANNER_IDS)
def test_flow_14_notifications(page, server_url, banner_id) -> None:
    page.goto(f"{server_url}/notifications?banner={banner_id}")
    page.wait_for_load_state("networkidle")
    page.locator(f'[data-testid="banner-{banner_id}"]').wait_for(state="visible", timeout=10_000)
