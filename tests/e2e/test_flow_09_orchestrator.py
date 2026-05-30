"""E2E flow 09: Orchestrator staging panel + quiescence-sync trigger.

Frontend Spec §6.8 (staging panel), Backend Spec §13 (orchestrator mode).
The operator-free per-file NAS sync redesign (2026-05-21) replaced the
``ingest.json`` five-state machine with the sync-queue job state.

The flow:

1. Plant a staged run via the test app's ``/staging?state=none`` route.
2. Verify the staging row renders with its state pill.
3. Click ``Force sync`` and verify the row's state advances to
   ``queued``.
"""

from __future__ import annotations

import pytest

from tests.e2e.page_objects.staging_page import StagingPage

# Orchestrator/staging is hidden at the UI layer; the staging dock is no longer
# reachable from the operator UI. The dock + test app /staging route are kept
# for reversibility. See
# docs/superpowers/specs/2026-05-29-hide-orchestrator-staging-design.md.
pytestmark = pytest.mark.skip(
    reason="orchestrator/staging hidden — see "
    "docs/superpowers/specs/2026-05-29-hide-orchestrator-staging-design.md"
)


def test_flow_09_orchestrator(page, server_url) -> None:
    staging = StagingPage(page)
    page.goto(f"{server_url}/staging?state=none")
    page.wait_for_load_state("networkidle")

    staging.dock.wait_for(state="visible", timeout=10_000)
    staging.row(0).wait_for(state="visible", timeout=5_000)
    assert "none" in staging.row(0).inner_text().lower()

    staging.force_sync(0).click()
    page.wait_for_load_state("networkidle")
    staging.row(0).wait_for(state="visible", timeout=5_000)
    assert "queued" in staging.row(0).inner_text().lower()
