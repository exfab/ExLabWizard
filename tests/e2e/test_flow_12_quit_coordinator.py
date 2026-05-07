"""E2E flow 12: Quit coordinator (drain in-flight sessions).

Frontend Spec §6.11 (quit-confirmation dialog), Backend Spec §13.5
(quit coordinator + tray quit hand-off).

This flow exercises a tray-subprocess interaction (pystray icon menu
quit -> coordinator drain -> tray-window-shutdown handshake). Headless
Chromium driving a uvicorn-only server cannot reproduce that surface
because the tray and the window run in separate OS processes. The
behaviour is fully covered by the Phase 13 unit + integration suites
(``tests/integration/test_tray_lifecycle.py`` and
``tests/unit/tray/test_quit_coordinator.py``).
"""

from __future__ import annotations

import pytest


@pytest.mark.skip(
    reason=(
        "exercises tray subprocess + pywebview window quit handshake; "
        "covered by Phase 13 unit tests + tests/integration/test_tray_lifecycle.py; "
        "not reachable from headless Chromium"
    ),
)
def test_flow_12_quit_coordinator(page, server_url) -> None:
    pass
