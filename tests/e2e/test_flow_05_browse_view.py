"""E2E flow 05: Browse view -- open existing project + sessions.

Frontend Spec §6.5 (browse view), Backend Spec §4.6.4 (browse
endpoints).

Status: SKIPPED in Phase 16 initial cut. The flow is documented in
``tests/e2e/README.md`` and will be implemented in a Phase 16
follow-up once the Phase 12 NiceGUI components carry ``data-testid``
attributes.
"""

from __future__ import annotations

import pytest


@pytest.mark.skip(
    reason="Phase 16 follow-up: requires data-testid attributes on Phase 12 components",
)
def test_flow_05_browse_view(page, server_url) -> None:
    pass
