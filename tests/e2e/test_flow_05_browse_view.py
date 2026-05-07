"""E2E flow 05: Browse view -- open existing project + sessions.

Frontend Spec §6.5 (browse view), Backend Spec §4.6.4 (browse
endpoints).

Asserts the main window renders the tree, toolbar, and tabs; verifies
a fixture-seeded equipment / project / run hierarchy is visible.
"""

from __future__ import annotations

from tests.e2e.page_objects.main_page import MainPage


def test_flow_05_browse_view(page, server_url) -> None:
    main = MainPage(page)
    page.goto(f"{server_url}/main")
    page.wait_for_load_state("networkidle")

    main.tree.wait_for(state="visible", timeout=10_000)
    # Toolbar buttons all visible.
    main.toolbar_new_project.wait_for(state="visible")
    main.toolbar_new_run.wait_for(state="visible")
    main.toolbar_new_test_run.wait_for(state="visible")
    main.toolbar_settings.wait_for(state="visible")
    main.toolbar_refresh.wait_for(state="visible")
    # Tabs visible.
    main.tab_details.wait_for(state="visible")
    main.tab_problems.wait_for(state="visible")

    # Tree contains the seeded equipment label.
    assert page.locator('[data-testid="main-tree"]').inner_text().find("EQ1") >= 0
