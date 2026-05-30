"""E2E flow 28: Phase 4/5 polish — file selection, search, density, legend.

Covers the surfaces added after the original Phase-9/10 file-explorer
flows and not exercised elsewhere:

* **Phase 4 (Option B):** single-clicking a file row highlights it
  (``data-selected``) and appends the selected-file sub-card
  (``metadata-selected-file``) in the metadata popover, while the
  selected run's metadata stays.
* **Phase 5:** the search box filters the tree and shows a result-count /
  no-matches pill; the Files-header density toggle threads ``?density=``;
  the sync-status legend popover opens; the selected tree node carries
  Quasar's ``q-tree__node--selected`` class (the unified-selection hook).

These run against the seeded ``_test_app`` ``/main`` handler, which drives
the production ``render_file_explorer_page`` directly (see flow 25).
"""

from __future__ import annotations

# The seeded run whose folder feed serves the four default file rows
# (scan.tif / metadata.json / frames.raw / archived.tif).
_RUN = "TEST_EQ1/Demo Project/Run_2026-05-07"


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


def test_flow_28_select_file_row_highlights_and_shows_subcard(page, server_url) -> None:
    """Single-clicking a file row threads ?file=, marks the row selected, and
    surfaces the metadata sub-card while the run metadata stays (Phase 4)."""
    _goto(page, f"{server_url}/main?selected={_RUN}")
    rows = page.locator('[data-testid="file-list-row"]')
    rows.first.wait_for(state="visible", timeout=10_000)
    rows.first.click()
    # The click re-navigates with the file path on the URL.
    page.wait_for_url(lambda url: "file=" in url, timeout=10_000)
    # The clicked row now carries data-selected, and the selected-file sub-card
    # appears in the metadata popover.
    page.locator('[data-testid="file-list-row"][data-selected="true"]').wait_for(
        state="visible", timeout=5_000
    )
    page.locator('[data-testid="metadata-selected-file"]').wait_for(state="visible", timeout=5_000)
    # The run's own metadata pane (run-context) remains alongside the sub-card.
    page.locator('[data-testid="metadata-pane"]').wait_for(state="visible", timeout=5_000)


def test_flow_28_search_filters_tree_and_shows_count(page, server_url) -> None:
    """A search query renders the result-count pill and seeds the box (OQ-2)."""
    _goto(page, f"{server_url}/main?q=Demo")
    count = page.locator('[data-testid="main-search-count"]')
    count.wait_for(state="visible", timeout=10_000)
    # The seeded query surfaced exactly the Demo project + its 3 runs.
    assert count.inner_text().strip() == "4 results"


def test_flow_28_search_no_matches_state(page, server_url) -> None:
    """A query that matches nothing renders the no-matches hint (OQ-2)."""
    _goto(page, f"{server_url}/main?q=zzz-no-such-run")
    page.locator('[data-testid="main-search-no-matches"]').wait_for(state="visible", timeout=10_000)


def test_flow_28_density_toggle_threads_density_param(page, server_url) -> None:
    """The Files-header density toggle re-navigates with ?density=compact (§4.8)."""
    _goto(page, f"{server_url}/main?selected={_RUN}")
    toggle = page.locator('[data-testid="files-density-toggle"]')
    toggle.wait_for(state="visible", timeout=10_000)
    toggle.click()
    page.wait_for_url(lambda url: "density=compact" in url, timeout=10_000)


def test_flow_28_sync_legend_popover_lists_states(page, server_url) -> None:
    """The Files-header "?" opens the sync-status legend popover (Phase 5)."""
    _goto(page, f"{server_url}/main?selected={_RUN}")
    legend = page.locator('[data-testid="files-legend"]')
    legend.wait_for(state="visible", timeout=10_000)
    legend.click()
    menu = page.locator('[data-testid="files-legend-menu"]')
    menu.wait_for(state="visible", timeout=5_000)
    # A couple of known meanings from _STATUS_TO_PROPS are listed.
    assert "Synced and verified at NAS" in menu.inner_text()


def test_flow_28_selected_tree_node_carries_selected_class(page, server_url) -> None:
    """The selected tree node renders Quasar's selected class -- the hook the
    unified-selection theme rule styles (OQ-6)."""
    _goto(page, f"{server_url}/main?selected={_RUN}")
    page.locator(".q-tree__node-header.q-tree__node--selected").wait_for(
        state="visible", timeout=10_000
    )
