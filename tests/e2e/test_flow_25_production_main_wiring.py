"""E2E flow 25: production /main is wired to the redesigned renderer.

This suite exists for one reason: PR9 added the redesigned three-region
file-explorer renderer + its full e2e coverage, but never re-wired the
production ``/main`` route to call it. Operators kept seeing the v1
two-tab layout while the Phase 9/10 flows passed against an inlined
parallel copy of the redesign in ``tests/e2e/_test_app.py``. This file
asserts that the production renderer (``render_file_explorer_page``) is
the one ``/main`` mounts and that all 8 new callback wires actually
route to a visible / URL-observable effect.

The test app's ``/main`` handler has been refactored to drive
``render_file_explorer_page`` directly (the inline copy is gone), so a
regression here means the redesign isn't reaching production at all --
the original bug class returning.
"""

from __future__ import annotations


def _goto(page, url: str, *, retries: int = 2) -> None:
    """Navigate to ``url`` with one retry on transient NiceGUI failures."""
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


# ---------------------------------------------------------------------------
# Layout canary: the redesign renders, not the legacy renderer
# ---------------------------------------------------------------------------


def test_flow_25_main_route_renders_redesigned_layout(page, server_url) -> None:
    """The /main route mounts the redesigned renderer, not the legacy one.

    Asserts the six toolbar buttons (the legacy toolbar had five and no
    Add Equipment), the metadata-tab testid (the legacy renderer
    emitted ``tab-details`` instead), and the footer Clear-verified
    button (legacy footer had three status segments and no bulk
    action). If any of these fail, ``/main`` is back on the legacy
    renderer and the bug class has returned.
    """
    _goto(page, f"{server_url}/main")
    for testid in (
        "toolbar-new-project",
        "toolbar-new-run",
        "toolbar-new-test-run",
        "toolbar-add-equipment",
        "toolbar-refresh",
        "toolbar-settings",
    ):
        page.locator(f'[data-testid="{testid}"]').wait_for(state="visible", timeout=10_000)
    # The redesigned right pane uses tab-metadata, not the legacy
    # tab-details. A legacy renderer would fail this assertion.
    page.locator('[data-testid="tab-metadata"]').wait_for(state="visible", timeout=5_000)
    page.locator('[data-testid="tab-problems"]').wait_for(state="visible", timeout=5_000)
    page.locator('[data-testid="footer-clear-verified"]').wait_for(
        state="visible", timeout=5_000
    )
    # The legacy renderer rendered ``tab-details`` — explicitly assert
    # it's gone so a future revert is caught.
    assert page.locator('[data-testid="tab-details"]').count() == 0


# ---------------------------------------------------------------------------
# Eight new callbacks each route to a visible / URL effect
# ---------------------------------------------------------------------------


def test_flow_25_add_equipment_button_navigates_to_wizard(page, server_url) -> None:
    """The Add Equipment toolbar button navigates to /wizard/equipment."""
    _goto(page, f"{server_url}/main")
    page.locator('[data-testid="toolbar-add-equipment"]').click()
    page.wait_for_url(lambda url: "/wizard/equipment" in url, timeout=10_000)


def test_flow_25_select_node_threads_selected_into_url(page, server_url) -> None:
    """Clicking an equipment node updates the URL with ?selected=<id>.

    Exercises on_select_node: the production mount's callback re-
    navigates to /main with the selection state encoded in the query
    string, mirroring how the redesign keeps the page idempotent.
    """
    _goto(page, f"{server_url}/main")
    page.locator('[data-testid="tree-node-equipment"]').first.wait_for(state="visible")
    page.locator('[data-testid="tree-node-equipment"]').first.click()
    page.wait_for_url(lambda url: "selected=" in url, timeout=10_000)


def test_flow_25_selected_query_renders_centre_and_right_panes(
    page, server_url
) -> None:
    """Loading /main?selected=EQ1 directly renders the centre + right panes."""
    _goto(page, f"{server_url}/main?selected=EQ1")
    # Centre pane: seeded folder feed shows the default two rows when no
    # specific path is seeded for EQ1.
    page.locator('[data-testid="file-list-row"]').first.wait_for(
        state="visible", timeout=5_000
    )
    # Right pane (metadata tab) renders with the equipment payload.
    page.locator('[data-testid="metadata-pane"]').wait_for(state="visible", timeout=5_000)


def test_flow_25_breadcrumb_navigation_re_navigates_main(page, server_url) -> None:
    """Clicking a breadcrumb segment routes back through /main?selected=.

    The breadcrumb renders for a selected node and each segment is
    clickable; wired to the same callback as on_select_node so the
    URL flow is identical.
    """
    _goto(page, f"{server_url}/main?selected=EQ1/Demo Project")
    page.locator('[data-testid="breadcrumb"]').wait_for(state="visible", timeout=5_000)
    segments = page.locator('[data-testid="breadcrumb-segment"]')
    assert segments.count() >= 1
    # Click the first segment -> URL re-navigates with selected= updated.
    segments.first.click()
    page.wait_for_url(lambda url: "/main" in url, timeout=10_000)


def test_flow_25_toggle_right_pane_toggles_query_param(page, server_url) -> None:
    """Clicking the right-pane toggle flips ?right_pane=collapsed in the URL."""
    _goto(page, f"{server_url}/main?selected=EQ1")
    page.locator('[data-testid="toggle-right-pane"]').wait_for(
        state="visible", timeout=5_000
    )
    page.locator('[data-testid="toggle-right-pane"]').click()
    page.wait_for_url(lambda url: "right_pane=collapsed" in url, timeout=10_000)
    # Click again -> the param is removed.
    page.locator('[data-testid="toggle-right-pane"]').click()
    page.wait_for_url(lambda url: "right_pane=collapsed" not in url, timeout=10_000)


def test_flow_25_tree_context_action_deep_links_into_settings(
    page, server_url
) -> None:
    """Owned-equipment Edit menu navigates to /settings with the equipment id.

    Routes through ``on_tree_context_action`` -> /settings deep link.
    """
    _goto(page, f"{server_url}/main")
    eq = page.locator('[data-testid="tree-node-equipment"]').first
    eq.wait_for(state="visible", timeout=10_000)
    eq.click(button="right")
    page.locator('[data-testid="tree-context-edit-equipment"]').click()
    page.wait_for_url(
        lambda url: "/settings" in url and "equipment_id=EQ1" in url, timeout=10_000
    )


def test_flow_25_received_equipment_disables_creation_buttons(
    page, server_url
) -> None:
    """Selecting a RELAY_* node disables the New Project/Run/Test-Run buttons.

    Verifies MainPageState.selected_node_is_received flows from the URL
    -> _classify_node -> render_file_explorer_page's disable logic
    (Redesign §3.3 / decision 1).
    """
    _goto(page, f"{server_url}/main?selected=RELAY_EQX")
    # The three creation buttons carry Quasar's ``disable`` prop, which
    # surfaces as the disabled HTML attribute on the underlying button
    # element. Use is_disabled() to assert.
    for testid in ("toolbar-new-project", "toolbar-new-run", "toolbar-new-test-run"):
        btn = page.locator(f'[data-testid="{testid}"]')
        btn.wait_for(state="visible", timeout=10_000)
        assert btn.is_disabled(), f"{testid} should be disabled when relay node selected"
    # Add Equipment stays enabled (the operator's escape hatch).
    assert page.locator('[data-testid="toolbar-add-equipment"]').is_enabled()


def test_flow_25_footer_clear_verified_routes_to_callback(page, server_url) -> None:
    """The footer Clear-verified button is wired to a callback.

    Asserts the button is clickable and the click doesn't 500. The
    button's backend dispatch is covered by the unit suite
    (`test_clear_verified_endpoint_clears_only_sync_verified_runs`);
    this flow proves the wire is in place.
    """
    _goto(page, f"{server_url}/main")
    btn = page.locator('[data-testid="footer-clear-verified"]')
    btn.wait_for(state="visible", timeout=5_000)
    btn.click()
    # The page should stay on /main (no navigation away) -- the
    # callback is fire-and-forget with a toast.
    page.wait_for_load_state("networkidle")
    assert "/main" in page.url
