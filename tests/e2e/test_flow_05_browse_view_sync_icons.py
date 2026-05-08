"""E2E flow 05b: per-run sync icons in the browse tree.

Verifies that the project / equipment tree (Frontend §3.5) renders the
correct SVG icon to the left of each run name based on its sync status:

* ``sync_status != "cleaned"`` (or absent) -> ``/assets/sync_local.svg``
* ``sync_status == "cleaned"``             -> ``/assets/sync_cloud.svg``

Also asserts the static asset mount actually serves the SVGs (200 OK)
so a missing PyInstaller bundle entry would surface here.
"""

from __future__ import annotations

import httpx

from tests.e2e.page_objects.main_page import MainPage


def test_flow_05_sync_icons_render_in_tree(page, server_url) -> None:
    main = MainPage(page)
    page.goto(f"{server_url}/main")
    page.wait_for_load_state("networkidle")
    main.tree.wait_for(state="visible", timeout=10_000)

    tree = page.locator('[data-testid="main-tree"]')

    # The seeded hierarchy in tests/e2e/_test_app.py contains:
    #   - Run_2026-05-07 (local;   sync_status=None)    -> sync_local.svg
    #   - Run_2026-05-06 (cleaned; sync_status=cleaned) -> sync_cloud.svg
    #   - TestRun_2026-05-07 (local; sync_status=None)  -> sync_local.svg
    local_icons = tree.locator('img[src="/assets/sync_local.svg"]')
    cloud_icons = tree.locator('img[src="/assets/sync_cloud.svg"]')

    # Two local runs (one experimental, one test) and one cleaned run.
    assert local_icons.count() == 2, f"expected 2 sync_local icons, got {local_icons.count()}"
    assert cloud_icons.count() == 1, f"expected 1 sync_cloud icon, got {cloud_icons.count()}"

    # The cleaned-run icon's parent header carries the canonical sync_status
    # marker on the label span (set by the default-header slot template).
    cloud_header = cloud_icons.first.locator("xpath=..")
    assert cloud_header.locator('span[data-sync-status="cleaned"]').count() == 1, (
        "cleaned run header missing data-sync-status='cleaned' marker"
    )


def test_flow_05_sync_icons_static_assets_serve_200(server_url) -> None:
    """The ``/assets`` mount serves both SVGs as 200 OK."""

    for url in (f"{server_url}/assets/sync_local.svg", f"{server_url}/assets/sync_cloud.svg"):
        response = httpx.get(url, timeout=5.0)
        assert response.status_code == 200, f"{url} returned {response.status_code}"
        body = response.text
        assert body.lstrip().startswith("<svg"), f"{url} did not return an SVG payload"
