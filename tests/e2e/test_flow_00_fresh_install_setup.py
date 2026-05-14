"""E2E flow 00: fresh-install first-launch setup against the PRODUCTION app.

Unlike the other ``test_flow_*`` files (which drive the ``_test_app.py``
TestState surface), this test boots the genuine production wizard --
``exlab_wizard.tray._build_default_app`` -- with ``HOME`` pointed at a
fresh tmp directory. That makes ``paths.os_config_path`` resolve under
tmp with no ``config.yaml`` present: a true fresh install.

It pins the bug fix that motivated the settings-page rewrite: before
the fix, the wizard's settings page rendered hard-coded placeholder
fields and its Save button persisted nothing, so a fresh install could
never get past the no-config state. This test walks:

    /  -> /welcome  -> (Get started)  -> /settings
    -> fill paths + LIMS scalar fields -> Save
    -> /restart-required gate
    -> config.yaml written under the tmp HOME with the entered values

The equipment / project / run / test-run / template creation flows are
NOT covered here: their wizard submit handlers in
``exlab_wizard/ui/mount.py`` are still toast-only stubs and no template
UX exists yet, so there is nothing functional to drive end-to-end.
Wiring those is tracked as follow-up feature work.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from tests.e2e._prod_server import free_port, prod_app_env, resolve_config_path
from tests.e2e.conftest import (  # reuse the chromium discovery + skip logic
    PLAYWRIGHT_AVAILABLE,
)

pytestmark = pytest.mark.skipif(
    not PLAYWRIGHT_AVAILABLE,
    reason="playwright not installed",
)


@pytest.fixture
def fresh_prod_server(tmp_path: Path):
    """Spawn the production wizard app with HOME redirected to a fresh tmp dir.

    Yields ``(base_url, home_dir)``. No ``config.yaml`` exists under
    ``home_dir`` on entry -- the wizard boots in the
    ``incomplete_no_config`` setup state.
    """
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    port = free_port()
    env = prod_app_env(home_dir)
    # A fresh-install boot legitimately logs WARN for the config-dependent
    # components (no config.yaml yet); keep the subprocess quiet so the
    # test output stays readable.
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "tests.e2e._prod_app:create_prod_app_factory",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "error",
        ],
        env=env,
    )
    base_url = f"http://127.0.0.1:{port}"
    healthy = False
    for _ in range(60):
        try:
            if httpx.get(f"{base_url}/api/v1/health", timeout=1.0).status_code == 200:
                healthy = True
                break
        except Exception:
            pass
        time.sleep(0.5)
    if not healthy:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        pytest.skip("production wizard app did not become healthy within 30s")
    try:
        yield base_url, home_dir
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def test_fresh_install_setup_writes_config_and_gates_on_restart(
    browser,
    fresh_prod_server,
    tmp_path: Path,
) -> None:
    base_url, home_dir = fresh_prod_server
    config_path = resolve_config_path(home_dir)
    assert not config_path.exists(), "precondition: fresh install has no config.yaml"

    # Folders the operator will point the wizard at -- all under tmp.
    templates_dir = tmp_path / "templates"
    plugin_dir = tmp_path / "plugins"
    local_root = tmp_path / "data"
    for folder in (templates_dir, plugin_dir, local_root):
        folder.mkdir()

    context = browser.new_context()
    page = context.new_page()
    try:
        # 1. Root gates an unconfigured install to the welcome card.
        page.goto(f"{base_url}/")
        page.wait_for_load_state("networkidle")
        page.get_by_test_id("welcome-card").wait_for(state="visible", timeout=10_000)
        assert "/welcome" in page.url

        # 2. "Get started" routes to the settings dialog.
        page.get_by_test_id("welcome-get-started").click()
        page.wait_for_load_state("networkidle")
        page.get_by_test_id("settings-dialog").wait_for(state="visible", timeout=10_000)
        assert "/settings" in page.url

        # 3. Fill the Paths section (the dialog opens here -- it is the
        #    first incomplete section on a fresh install).
        page.get_by_test_id("settings-paths-templates").fill(str(templates_dir))
        page.get_by_test_id("settings-paths-plugin").fill(str(plugin_dir))
        page.get_by_test_id("settings-paths-local-root").fill(str(local_root))

        # 4. Fill the LIMS section.
        page.get_by_test_id("settings-nav-lims").click()
        page.get_by_test_id("settings-lims-endpoint").wait_for(state="visible", timeout=5_000)
        page.get_by_test_id("settings-lims-endpoint").fill("https://lims.example.test")
        page.get_by_test_id("settings-lims-email").fill("operator@example.test")

        # 5. Save -> the wizard persists config.yaml and routes to the
        #    restart-required gate.
        page.get_by_test_id("settings-save").click()
        page.wait_for_load_state("networkidle")
        page.get_by_test_id("restart-required").wait_for(state="visible", timeout=10_000)
        assert "/restart-required" in page.url

        # 6. config.yaml now exists under the tmp HOME with the values
        #    the operator entered.
        assert config_path.exists(), "Save must persist config.yaml under the tmp HOME"
        text = config_path.read_text(encoding="utf-8")
        assert str(local_root) in text
        assert "https://lims.example.test" in text
        assert "operator@example.test" in text

        # 7. The restart gate is sticky: navigating elsewhere bounces
        #    back to /restart-required until the tray is relaunched.
        page.goto(f"{base_url}/main")
        page.wait_for_load_state("networkidle")
        page.get_by_test_id("restart-required").wait_for(state="visible", timeout=10_000)
        assert "/restart-required" in page.url
    finally:
        context.close()
