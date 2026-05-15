"""E2E flow 16: Add-Equipment wizard (Redesign §6).

Drives the five-step wizard end to end against the test app:
identity → paths → sync_mode → signal → review → confirm.

The test app mounts the wizard at ``/wizard/equipment?step=<step>`` so
each step can be loaded directly; the production navigation between
steps is exercised in the unit tests
(``tests/unit/ui/test_wizard_equipment.py``). This flow proves the
NiceGUI render path produces every testid the catalog promises.
"""

from __future__ import annotations

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
    """Paths step renders local + NAS root inputs."""
    wiz = WizardEquipmentPage(page)
    _goto(page, f"{server_url}/wizard/equipment?step=paths")
    wiz.local_root.wait_for(state="visible", timeout=10_000)
    wiz.nas_root.wait_for(state="visible")


def test_flow_16_add_equipment_sync_mode_step(page, server_url) -> None:
    """Sync mode step renders the nas/stage radio."""
    wiz = WizardEquipmentPage(page)
    _goto(page, f"{server_url}/wizard/equipment?step=sync_mode")
    wiz.sync_mode.wait_for(state="visible", timeout=10_000)


def test_flow_16_add_equipment_signal_step(page, server_url) -> None:
    """Completeness signal step renders the radio + sentinel filename."""
    wiz = WizardEquipmentPage(page)
    _goto(page, f"{server_url}/wizard/equipment?step=signal")
    wiz.signal.wait_for(state="visible", timeout=10_000)
    wiz.sentinel_filename.wait_for(state="visible")


def test_flow_16_add_equipment_review_and_confirm(page, server_url) -> None:
    """Review step renders Confirm; clicking it fires the success label."""
    wiz = WizardEquipmentPage(page)
    _goto(page, f"{server_url}/wizard/equipment?step=review")
    wiz.confirm.wait_for(state="visible", timeout=10_000)
    wiz.confirm.click()
    page.wait_for_load_state("networkidle")
    wiz.success.wait_for(state="visible", timeout=5_000)
