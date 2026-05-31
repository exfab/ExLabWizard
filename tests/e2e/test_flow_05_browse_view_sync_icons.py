"""E2E flow 05b: per-run sync rollup icons in the browse tree.

Two-icon sync-presence design (2026-05-30). The project / equipment
tree (Frontend §3.5) renders a single colour-coded rollup icon to the
left of each run name, derived from ``file_sync_view(sync_status)`` ->
``sync_rollup_icon_props(view)``:

* ``syncing`` / ``pending`` / ``local_only``  -> LOCAL_ONLY    -> ``/assets/sync_local.svg`` (blue)
* ``synced`` / ``cleared`` / ``on_nas``       -> ON_NAS/SYNCED -> ``/assets/sync_nas.svg``   (green)
* ``upload_failed`` / ``failed``              -> UPLOAD_FAILED -> ``/assets/sync_nas.svg``   (red,  ✕ badge)
* ``blocked`` / ``blocked_by_validation``     -> BLOCKED       -> ``/assets/sync_nas.svg``   (amber, ! badge)
* ``None`` / ``""`` / unknown                 -> NONE          -> no icon rendered

The seeded hierarchy in ``tests/e2e/_test_app.py`` carries one run of
each visible state (plus a NONE test-run that renders nothing), so the
tree shows exactly one ``sync_local.svg`` and three ``sync_nas.svg``
icons, one problem background, and two badges.

Also asserts the static asset mount serves the SVGs (200 OK) so a
missing PyInstaller bundle entry would surface here. The retired cloud
asset is no longer referenced anywhere.
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

    # Seeded runs under TEST_EQ1 / LIMS-001:
    #   Run_2026-05-07 (syncing)       -> LOCAL_ONLY    -> sync_local.svg
    #   Run_2026-05-06 (cleared)       -> ON_NAS        -> sync_nas.svg (green)
    #   Run_2026-05-05 (upload_failed) -> UPLOAD_FAILED -> sync_nas.svg (red,  ✕)
    #   Run_2026-05-04 (blocked)       -> BLOCKED       -> sync_nas.svg (amber, !)
    #   TestRun_2026-05-07 (None)      -> NONE          -> no icon
    local_icons = tree.locator('img[src="/assets/sync_local.svg"]')
    nas_icons = tree.locator('img[src="/assets/sync_nas.svg"]')

    # At least one local-only (blue) run and at least one NAS-presence run.
    assert local_icons.count() >= 1, f"expected >= 1 sync_local icon, got {local_icons.count()}"
    assert nas_icons.count() >= 1, f"expected >= 1 sync_nas icon, got {nas_icons.count()}"

    # The failed run carries the problem (red) background...
    problem_bg = tree.locator('span[data-sync-bg="--color-sync-problem"]')
    assert problem_bg.count() >= 1, (
        f"expected >= 1 problem-background rollup, got {problem_bg.count()}"
    )

    # ...and a corner badge (✕ for failed, ! for blocked) is rendered.
    badges = tree.locator('span[data-sync-badge="true"]')
    assert badges.count() >= 1, f"expected >= 1 sync badge, got {badges.count()}"


def test_flow_05_sync_icons_static_assets_serve_200(server_url) -> None:
    """The ``/assets`` mount serves both presence SVGs as 200 OK."""

    for url in (f"{server_url}/assets/sync_local.svg", f"{server_url}/assets/sync_nas.svg"):
        response = httpx.get(url, timeout=5.0)
        assert response.status_code == 200, f"{url} returned {response.status_code}"
        body = response.text
        assert body.lstrip().startswith("<svg"), f"{url} did not return an SVG payload"
