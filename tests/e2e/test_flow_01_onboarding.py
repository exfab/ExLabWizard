"""E2E flow 01: First-launch onboarding.

Frontend Spec §6.1 (welcome card + autostart toggle), §6.2.4 (setup
gate banner).

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
def test_flow_01_onboarding(page, server_url) -> None:
    pass
