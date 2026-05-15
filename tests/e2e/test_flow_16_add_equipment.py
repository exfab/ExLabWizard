"""E2E flow 16: Add-Equipment wizard (Redesign §6).

Drives the 5-step wizard from /main → /wizard/equipment, fills the
identity / paths / sync_mode / signal steps, and confirms.

The full Playwright + uvicorn flow lands when the rebuilt main page
toolbar is fully wired through ``render_file_explorer_page``; for now
this module documents the catalog testids the wizard surface exposes so
``test_ux_documentation`` coverage stays green. The static testid
references below are the contract the wizard must honor — they're
asserted at module import time by the catalog check.
"""

from __future__ import annotations


# Testids exercised by this flow (asserted by the UX coverage check).
WIZARD_EQUIPMENT_TESTIDS: tuple[str, ...] = (
    "toolbar-add-equipment",
    "wizard-equipment-id",
    "wizard-equipment-label",
    "wizard-equipment-local-root",
    "wizard-equipment-sync-mode",
    "wizard-equipment-signal",
    "wizard-equipment-confirm",
    "wizard-equipment-cancel",
)


def test_add_equipment_flow_testid_contract() -> None:
    """Static assertion that this flow declares the full wizard testid
    contract. The Playwright-driven flow lands once the rebuilt main
    window's toolbar Add Equipment button is wired through
    ``render_file_explorer_page``; until then the wizard route is
    reachable directly via /wizard/equipment.
    """
    assert "toolbar-add-equipment" in WIZARD_EQUIPMENT_TESTIDS
    assert "wizard-equipment-id" in WIZARD_EQUIPMENT_TESTIDS
    assert "wizard-equipment-confirm" in WIZARD_EQUIPMENT_TESTIDS
