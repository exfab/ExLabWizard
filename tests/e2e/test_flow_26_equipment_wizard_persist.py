"""E2E flow 26: Add-Equipment wizard confirm against the PRODUCTION app.

``test_flow_16`` drives the wizard's four-step *render* against the
``_test_app.py`` TestState surface. This test boots the genuine
production app (``exlab_wizard.tray._build_default_app``) under a fresh
tmp ``HOME`` and drives the wizard end to end -- type, Next, Confirm --
then asserts the equipment was actually *persisted* to ``config.yaml``.

Regression guard for three production bugs that flow_16 could never
see, because the test app pre-seeded a valid state and supplied its own
confirm handler:

* the Next button's ``disable`` was frozen at render time, so typing a
  valid identity never re-enabled it;
* the ``/wizard/equipment`` route rebuilt an empty wizard state on every
  step navigation, discarding entered fields;
* confirm was wired to a ``deps.append_equipment`` attribute that the
  production dependency factory never populated -- so confirm silently
  persisted nothing.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from tests.e2e._prod_server import ProdServer
from tests.e2e.conftest import PLAYWRIGHT_AVAILABLE
from tests.e2e.page_objects.wizard_equipment_page import WizardEquipmentPage

pytestmark = pytest.mark.skipif(
    not PLAYWRIGHT_AVAILABLE,
    reason="playwright not installed",
)


@pytest.fixture
def prod_server(tmp_path: Path):
    """Spawn the production wizard app under a fresh tmp HOME."""
    home = tmp_path / "home"
    home.mkdir()
    server = ProdServer(home)
    if not server.start():
        pytest.skip("production wizard app did not become healthy within 30s")
    try:
        yield server
    finally:
        server.stop()


def test_equipment_wizard_confirm_persists_to_config(browser, prod_server) -> None:
    """Drive the production wizard to Confirm and assert config.yaml gains the device."""
    assert not prod_server.config_path.exists(), "precondition: fresh install, no config.yaml"

    context = browser.new_context()
    page = context.new_page()
    try:
        wiz = WizardEquipmentPage(page)
        page.goto(f"{prod_server.base_url}/wizard/equipment")
        page.wait_for_load_state("networkidle")
        wiz.step_identity.wait_for(state="visible", timeout=10_000)

        # 1. Identity.
        wiz.equipment_id.fill("MICROSCOPE_01")
        wiz.label.fill("Confocal Microscope 1")
        wiz.next_button.click()

        # 2. Paths. The sync-mode step is hidden (orchestrator/staging hidden —
        #    see docs/superpowers/specs/2026-05-29-hide-orchestrator-staging-design.md),
        #    so paths advances straight to review; every equipment is nas-mode.
        wiz.local_root.wait_for(state="visible", timeout=10_000)
        wiz.local_root.fill("/data/MICROSCOPE_01")
        wiz.nas_root.fill("/srv/nas/MICROSCOPE_01")
        wiz.next_button.click()

        # 3. Review -> Confirm.
        wiz.confirm.wait_for(state="visible", timeout=10_000)
        wiz.confirm.click()
        page.wait_for_load_state("networkidle")

        # ``_on_confirm`` writes config.yaml synchronously before it
        # navigates away, so once the click settles the file is there.
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not prod_server.config_path.exists():
            time.sleep(0.25)
        assert prod_server.config_path.exists(), "confirm must persist config.yaml"

        text = prod_server.config_path.read_text(encoding="utf-8")
        assert "MICROSCOPE_01" in text
        assert "Confocal Microscope 1" in text
    finally:
        context.close()
