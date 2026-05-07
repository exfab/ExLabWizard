"""E2E flow 07: Plugin input-required round trip.

Frontend Spec §6.4.5 (plugin step), Backend Spec §6.4
(``WAIT_PLUGIN_INPUT`` state + reply contract).

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
def test_flow_07_plugin_input_required(page, server_url) -> None:
    pass
