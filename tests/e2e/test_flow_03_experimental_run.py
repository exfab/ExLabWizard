"""E2E flow 03: Experimental-run wizard end-to-end.

Frontend Spec §6.4 (run wizard), Backend Spec §7.5 (experimental run
session machine).

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
def test_flow_03_experimental_run(page, server_url) -> None:
    pass
