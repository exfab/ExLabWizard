"""E2E flow 24: right-click context menus (Redesign §4.6 / decision 4A / §9.1).

Three context menus carry the redesign's relocated affordances:

1. Tree right-click on an **owned-equipment** node — *Edit equipment…*
   and *Remove…* both deep-link into Settings → Equipment List
   (decision 4A).
2. Tree right-click on a **run** node — *Force sync* / *Clear verified*
   / *View log* (the relocated bottom-dock per-run actions, §4.6).
3. File-list row right-click — *Open in OS* / *Copy path* (decision 6A:
   the right pane stays node-scoped, the context menu is the only thing
   a file-row selection drives).

Received-equipment nodes have no context menu (decision 3); their
"edit" surface is the producer device.
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


def _open_context(page, locator) -> None:
    """Open a NiceGUI ``ui.context_menu`` by right-clicking its anchor."""
    locator.click(button="right")


def test_flow_24_owned_equipment_context_menu_items_render(page, server_url) -> None:
    _goto(page, f"{server_url}/main?view=explorer")
    eq = page.locator('[data-testid="tree-node-equipment"]').first
    eq.wait_for(state="visible", timeout=10_000)
    _open_context(page, eq)
    page.locator('[data-testid="tree-context-edit-equipment"]').wait_for(
        state="visible", timeout=5_000
    )
    page.locator('[data-testid="tree-context-remove-equipment"]').wait_for(
        state="visible", timeout=5_000
    )


def test_flow_24_edit_equipment_deep_links_into_settings(page, server_url) -> None:
    _goto(page, f"{server_url}/main?view=explorer")
    eq = page.locator('[data-testid="tree-node-equipment"]').first
    eq.wait_for(state="visible", timeout=10_000)
    _open_context(page, eq)
    page.locator('[data-testid="tree-context-edit-equipment"]').click()
    page.wait_for_url(lambda url: "/settings" in url, timeout=10_000)
    assert "active=equipment" in page.url
    assert "equipment_id=EQ1" in page.url


def test_flow_24_remove_equipment_deep_links_into_settings(page, server_url) -> None:
    _goto(page, f"{server_url}/main?view=explorer")
    eq = page.locator('[data-testid="tree-node-equipment"]').first
    _open_context(page, eq)
    page.locator('[data-testid="tree-context-remove-equipment"]').click()
    page.wait_for_url(lambda url: "/settings" in url, timeout=10_000)
    assert "active=equipment" in page.url


def test_flow_24_received_equipment_has_no_context_menu(page, server_url) -> None:
    """Decision 3: received-equipment nodes don't expose edit / remove."""
    _goto(page, f"{server_url}/main?view=explorer")
    relay = page.locator('[data-testid="tree-node-received_equipment"]')
    relay.wait_for(state="visible", timeout=10_000)
    _open_context(page, relay)
    # Neither the edit nor the remove menu items should appear.
    assert page.locator('[data-testid="tree-context-edit-equipment"]').count() == 0 or \
        not page.locator('[data-testid="tree-context-edit-equipment"]').first.is_visible()


def test_flow_24_run_context_menu_items_render(page, server_url) -> None:
    _goto(page, f"{server_url}/main?view=explorer")
    run = page.locator('[data-testid="tree-node-run"]')
    run.wait_for(state="visible", timeout=10_000)
    _open_context(page, run)
    page.locator('[data-testid="run-context-force-sync"]').wait_for(
        state="visible", timeout=5_000
    )
    page.locator('[data-testid="run-context-clear-verified"]').wait_for(
        state="visible", timeout=5_000
    )
    page.locator('[data-testid="run-context-view-log"]').wait_for(
        state="visible", timeout=5_000
    )


def test_flow_24_file_list_row_context_menu(page, server_url) -> None:
    """Right-clicking a file-list row surfaces Open in OS + Copy path."""
    _goto(page, f"{server_url}/main?view=explorer")
    # Select a run so the centre pane renders the seeded file list.
    page.locator('[data-testid="tree-node-run"]').click()
    page.wait_for_load_state("networkidle")
    row = page.locator('[data-testid="file-list-row"]').first
    row.wait_for(state="visible", timeout=10_000)
    _open_context(page, row)
    page.locator('[data-testid="file-context-open-in-os"]').wait_for(
        state="visible", timeout=5_000
    )
    page.locator('[data-testid="file-context-copy-path"]').wait_for(
        state="visible", timeout=5_000
    )
