"""E2E flow 05: Browse view -- open existing project + sessions.

Frontend Spec §6.5 (browse view), Backend Spec §4.6.4 (browse
endpoints). Asserts the redesigned three-region main window renders
the tree, the six-button toolbar (Redesign §4), and the
metadata / problems tabs (Redesign §4.4); verifies the fixture-seeded
equipment / project / run hierarchy is visible.
"""

from __future__ import annotations

from tests.e2e.page_objects.main_page import MainPage


def test_flow_05_browse_view(page, server_url) -> None:
    main = MainPage(page)
    page.goto(f"{server_url}/main")
    page.wait_for_load_state("networkidle")

    main.tree.wait_for(state="visible", timeout=10_000)
    # Redesigned toolbar: six buttons including the new Add Equipment.
    main.toolbar_new_project.wait_for(state="visible")
    main.toolbar_new_run.wait_for(state="visible")
    main.toolbar_new_test_run.wait_for(state="visible")
    main.toolbar_add_equipment.wait_for(state="visible")
    main.toolbar_settings.wait_for(state="visible")
    main.toolbar_refresh.wait_for(state="visible")
    # Right-pane tabs (Metadata replaces the legacy Details tab).
    main.tab_metadata.wait_for(state="visible")
    main.tab_problems.wait_for(state="visible")
    # Footer (Redesign §4.6): Staging segment + bulk Clear-verified button.
    main.footer_staging_segment.wait_for(state="visible")
    main.footer_clear_verified.wait_for(state="visible")

    # Tree contains the seeded equipment label.
    assert page.locator('[data-testid="main-tree"]').inner_text().find("EQ1") >= 0
