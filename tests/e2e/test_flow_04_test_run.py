"""E2E flow 04: Test-run wizard end-to-end.

Frontend Spec §6.4 (run wizard, test mode), Backend Spec §7.6 (test
run session machine).

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
def test_flow_04_test_run(page, server_url) -> None:
    pass
