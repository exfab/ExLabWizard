"""E2E flow 02: Project wizard 7-step happy path.

Frontend Spec §6.3 (project wizard stepper).

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
def test_flow_02_project_wizard(page, server_url) -> None:
    pass
