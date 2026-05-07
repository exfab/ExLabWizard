"""E2E flow 09: Orchestrator staging panel + ingest.json watcher.

Frontend Spec §6.8 (staging panel), Backend Spec §13 (orchestrator
mode + ingest.json contract).

The flow:

1. Plant a staged run via the test app's ``/staging?state=staging`` route.
2. Verify the staging row renders with the right state pill.
3. Click ``Force sync`` and verify the row's state advances to
   ``sync_queued``.
"""

from __future__ import annotations

from tests.e2e.page_objects.staging_page import StagingPage


def test_flow_09_orchestrator(page, server_url) -> None:
    staging = StagingPage(page)
    page.goto(f"{server_url}/staging?state=staging")
    page.wait_for_load_state("networkidle")

    staging.dock.wait_for(state="visible", timeout=10_000)
    staging.row(0).wait_for(state="visible", timeout=5_000)
    assert "staging" in staging.row(0).inner_text().lower()

    staging.force_sync(0).click()
    page.wait_for_load_state("networkidle")
    staging.row(0).wait_for(state="visible", timeout=5_000)
    assert "sync_queued" in staging.row(0).inner_text().lower()
