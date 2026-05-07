"""E2E flow 10: Schema-mismatch / migration prompt.

Frontend Spec §6.9 (schema-mismatch dialog), Backend Spec §3.4 (cache
schema versioning).

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
def test_flow_10_schema_mismatch(page, server_url) -> None:
    pass
