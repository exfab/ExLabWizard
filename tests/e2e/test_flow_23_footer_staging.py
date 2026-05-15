"""E2E flow 23: footer Staging segment + bulk Clear verified (Redesign §4.6).

The bottom-dock staging panel is removed. Its bulk action ("Clear
verified runs") moves into the footer Staging segment's popover.
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


def test_flow_23_footer_staging_segment_renders(page, server_url) -> None:
    _goto(page, f"{server_url}/main?view=explorer")
    page.locator('[data-testid="footer-staging-segment"]').wait_for(
        state="visible", timeout=10_000
    )


def test_flow_23_bulk_clear_verified_action_present(page, server_url) -> None:
    _goto(page, f"{server_url}/main?view=explorer")
    btn = page.locator('[data-testid="footer-clear-verified"]')
    btn.wait_for(state="visible", timeout=10_000)
    btn.click()
    page.wait_for_load_state("networkidle")
