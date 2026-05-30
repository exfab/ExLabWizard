"""E2E flow 20: file-explorer navigation (Redesign §4).

Tree selection drives the centre-pane file list + right metadata
pane. Toolbar / breadcrumb / footer surfaces are all visible.
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


def test_flow_20_file_explorer_renders_three_regions(page, server_url) -> None:
    _goto(page, f"{server_url}/main?view=explorer")
    # Toolbar — every Redesign §4.7 button is present.
    for testid in (
        "toolbar-new-project",
        "toolbar-new-run",
        "toolbar-new-test-run",
        "toolbar-add-equipment",
        "toolbar-refresh",
        "toolbar-settings",
    ):
        page.locator(f'[data-testid="{testid}"]').wait_for(state="visible", timeout=10_000)
    # Tree has at least one equipment node.
    page.locator('[data-testid="tree-node-equipment"]').first.wait_for(state="visible")
    # Footer "Staging" segment + bulk clear-verified are intentionally absent —
    # orchestrator/staging hidden (see
    # docs/superpowers/specs/2026-05-29-hide-orchestrator-staging-design.md).
    # Per-run sync actions live on the metadata pane (see the next test).


def test_flow_20_select_run_node_renders_metadata_pane(page, server_url) -> None:
    _goto(page, f"{server_url}/main?view=explorer")
    page.locator('[data-testid="tree-node-run"]').first.wait_for(state="visible", timeout=10_000)
    page.locator('[data-testid="tree-node-run"]').first.click()
    page.wait_for_load_state("networkidle")
    page.locator('[data-testid="metadata-pane"]').wait_for(state="visible", timeout=5_000)
    page.locator('[data-testid="metadata-run-force-sync"]').wait_for(state="visible")
    page.locator('[data-testid="metadata-run-clear-verified"]').wait_for(state="visible")
    page.locator('[data-testid="metadata-run-view-log"]').wait_for(state="visible")


def test_flow_20_breadcrumb_is_present_when_node_selected(page, server_url) -> None:
    _goto(page, f"{server_url}/main?view=explorer")
    run = page.locator('[data-testid="tree-node-run"]').first
    run.wait_for(state="visible", timeout=10_000)
    run.click()
    page.wait_for_url(lambda url: "selected=" in url, timeout=10_000)
    page.locator('[data-testid="breadcrumb"]').wait_for(state="visible", timeout=5_000)
    segments = page.locator('[data-testid="breadcrumb-segment"]')
    # selecting the run leaf gives TEST_EQ1 / Demo Project / Run_...
    assert segments.count() >= 1
