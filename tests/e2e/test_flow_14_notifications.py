"""E2E flow 14: Tray notifications + in-app toasts.

Frontend Spec §6.13 (notification surface), Backend Spec §12.4 (tray
notifications) + §11.9 (in-app problem toasts).

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
def test_flow_14_notifications(page, server_url) -> None:
    pass
