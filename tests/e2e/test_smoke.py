"""E2E smoke flow.

Confirms the Phase 16 e2e harness can drive a real browser against a
real uvicorn server. The smoke flow does not depend on the NiceGUI
UI being mounted on ``/`` -- the conftest factory only mounts the
``/api/v1`` routers, so we verify the playwright page can drive the
server at all.
"""

from __future__ import annotations

# pytestmark from conftest.py controls skipping when playwright is missing.


def test_root_url_responds_with_html(page, server_url) -> None:
    """Smoke: navigate to / and confirm the e2e harness is wired up.

    The Phase 16 ``create_app()`` factory mounts ``/api/v1/...`` routers
    only -- no NiceGUI UI is mounted on ``/`` because the NiceGUI
    bootstrap is owned by the tray + window launchers. We therefore
    accept any HTTP response (200 or 404 with a body) at ``/`` as a
    pass; the load-bearing assertion is that the playwright page can
    drive the server at all.
    """
    page.goto(server_url)
    title = page.title()
    assert title is not None
    body_html = page.inner_html("body")
    assert isinstance(body_html, str)
