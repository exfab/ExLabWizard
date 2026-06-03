"""E2E flow 16: Add-Equipment wizard (Redesign §6).

Drives the wizard end to end against the test app:
identity → paths → review → confirm. The sync-mode step is hidden
(orchestrator/staging hidden — see
docs/superpowers/specs/2026-05-29-hide-orchestrator-staging-design.md), so
every equipment is created in nas mode.

The test app mounts the wizard at ``/wizard/equipment?step=<step>`` so
each step can be loaded directly; the production navigation between
steps is exercised in the unit tests
(``tests/unit/ui/test_wizard_equipment.py``). This flow proves the
NiceGUI render path produces every testid the catalog promises.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from tests.e2e.page_objects.wizard_equipment_page import WizardEquipmentPage


def _goto(page, url: str, *, retries: int = 2) -> None:
    last: Exception | None = None
    for _ in range(retries + 1):
        try:
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle")
            return
        except Exception as exc:
            last = exc
            page.wait_for_timeout(300)
    raise AssertionError(f"navigation to {url} failed: {last!r}")


def test_flow_16_add_equipment_identity_step(page, server_url) -> None:
    """Identity step renders the ID + label inputs."""
    wiz = WizardEquipmentPage(page)
    _goto(page, f"{server_url}/wizard/equipment?step=identity")
    wiz.step_identity.wait_for(state="visible", timeout=10_000)
    wiz.equipment_id.wait_for(state="visible")
    wiz.label.wait_for(state="visible")
    wiz.cancel.wait_for(state="visible")
    wiz.next_button.wait_for(state="visible")


def test_flow_16_add_equipment_paths_step(page, server_url) -> None:
    """Paths step renders the NAS root input (the data dir is derived)."""
    wiz = WizardEquipmentPage(page)
    _goto(page, f"{server_url}/wizard/equipment?step=paths")
    wiz.nas_root.wait_for(state="visible", timeout=10_000)


@pytest.mark.skip(
    reason="sync-mode wizard step hidden (orchestrator/staging hidden) — see "
    "docs/superpowers/specs/2026-05-29-hide-orchestrator-staging-design.md"
)
def test_flow_16_add_equipment_sync_mode_step(page, server_url) -> None:
    """Sync mode step renders the nas/stage radio."""
    wiz = WizardEquipmentPage(page)
    _goto(page, f"{server_url}/wizard/equipment?step=sync_mode")
    wiz.sync_mode.wait_for(state="visible", timeout=10_000)


def test_flow_16_add_equipment_review_and_confirm(page, server_url) -> None:
    """Review step renders Confirm; clicking it fires the success label."""
    wiz = WizardEquipmentPage(page)
    _goto(page, f"{server_url}/wizard/equipment?step=review")
    wiz.confirm.wait_for(state="visible", timeout=10_000)
    wiz.confirm.click()
    page.wait_for_load_state("networkidle")
    wiz.success.wait_for(state="visible", timeout=5_000)


def test_flow_16_next_enables_on_valid_input_and_state_survives_back(page, server_url) -> None:
    """An empty wizard: Next stays disabled until the identity step is
    valid, then a click advances *in place* and the entered data
    survives a Back step.

    Regression guard for two production bugs the seeded ``?step=`` tests
    could never see: the Next button's ``disable`` was frozen at render
    time (typing never re-enabled it), and the route rebuilt a fresh
    empty ``EquipmentWizardState`` on every step navigation.
    """
    wiz = WizardEquipmentPage(page)
    _goto(page, f"{server_url}/wizard/equipment?step=identity&seed=0")
    wiz.step_identity.wait_for(state="visible", timeout=10_000)

    # Bug A: Next is gated shut until the step validates.
    expect(wiz.next_button).to_be_disabled()
    wiz.equipment_id.fill("TEST_EQ1")
    wiz.label.fill("Lab Device")
    expect(wiz.next_button).to_be_enabled()

    # Advancing re-renders in place -- no page navigation.
    wiz.next_button.click()
    wiz.step_paths.wait_for(state="visible", timeout=10_000)

    # Bug B: stepping back keeps what the operator already typed.
    wiz.back.click()
    wiz.step_identity.wait_for(state="visible", timeout=10_000)
    expect(wiz.equipment_id).to_have_value("TEST_EQ1")
