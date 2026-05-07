"""E2E flow 12: Quit coordinator (drain in-flight sessions).

Frontend Spec §6.11 (quit-confirmation dialog), Backend Spec §13.5
(quit coordinator + tray quit hand-off).

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
def test_flow_12_quit_coordinator(page, server_url) -> None:
    pass
