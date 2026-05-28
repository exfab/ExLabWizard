"""E2E flow 27: NAS-credential setup gate + Settings section (rclone-only).

Boots the genuine production wizard (``_build_default_app``) against a
fresh tmp HOME seeded with a config carrying one nas-mode equipment but
no stored NAS password. The rclone-only migration (2026-05-26) made that
an ``INCOMPLETE_NO_NAS_CREDENTIAL`` setup state; this test walks the
recovery flow the operator actually uses:

    seeded config (nas equipment, no keyring entry)
      -> GET /setup/status == incomplete_no_nas_credential
      -> Settings -> NAS Credentials -> set password
      -> GET /setup/status == ready
      -> Test connection -> "Connected" (rclone about via stub)
      -> clear password
      -> GET /setup/status == incomplete_no_nas_credential

The keyring is forced onto the encrypted-at-rest fallback
(``PYTHON_KEYRING_BACKEND=keyring.backends.fail.Keyring`` +
``EXLAB_WIZARD_SECRET_PASSPHRASE``) so the credential round-trip is
deterministic and never prompts an OS keychain on a developer's machine.
The ``rclone`` probe is satisfied by the on-PATH ``stub_rclone`` binary.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from tests.e2e._prod_server import free_port, prod_app_env, resolve_config_path
from tests.e2e.conftest import PLAYWRIGHT_AVAILABLE

pytestmark = pytest.mark.skipif(
    not PLAYWRIGHT_AVAILABLE,
    reason="playwright not installed",
)


def _seed_config(home: Path, *, env: dict[str, str], local_root: Path, catalogue: Path) -> Path:
    """Write a config.yaml with one nas-mode equipment under the tmp HOME.

    LIMS is satisfied via ``offline_catalogue_path`` so the only
    outstanding gate is the NAS credential -- keeping the test focused on
    the rclone-only migration's new state.
    """
    import unittest.mock

    from exlab_wizard.config.loader import save_config
    from exlab_wizard.config.models import (
        Config,
        EquipmentConfig,
        LIMSConfig,
        OrchestratorConfig,
        PathsConfig,
        RcloneSftpTransport,
    )

    cfg = Config(
        paths=PathsConfig(
            templates_dir=str(local_root),
            plugin_dir=str(local_root),
            local_root=str(local_root),
        ),
        lims=LIMSConfig(offline_catalogue_path=str(catalogue)),
        equipment=[
            EquipmentConfig(
                id="EQ1",
                label="Equipment 1",
                local_root=str(local_root),
                nas_root="/srv/nas",
                transport=RcloneSftpTransport(
                    type="rclone_sftp",
                    host="nas.lab.example",
                    user="testuser",
                    remote_path="lab/EQ1",
                ),
            )
        ],
        orchestrator=OrchestratorConfig(label="LAB-1", staging_root=str(local_root / "staging")),
    )
    with unittest.mock.patch.dict(os.environ, env, clear=True):
        config_path = resolve_config_path(home)
        config_path.parent.mkdir(parents=True, exist_ok=True)
        save_config(config_path, cfg, original_text=None)
    return config_path


def _install_stub_rclone(bin_dir: Path) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    fixtures = Path(__file__).resolve().parents[1] / "fixtures"
    target = bin_dir / "rclone"
    shutil.copy(fixtures / "stub_rclone.py", target)
    st = target.stat()
    target.chmod(st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture
def nas_prod_server(tmp_path: Path):
    """Spawn the production wizard seeded with a credential-less nas equipment.

    Yields ``base_url``. The keyring is pinned to the encrypted fallback
    and ``rclone`` resolves to the test stub.
    """
    home = tmp_path / "home"
    home.mkdir()
    local_root = tmp_path / "data"
    local_root.mkdir()
    (local_root / "staging").mkdir()
    catalogue = tmp_path / "catalogue.json"
    catalogue.write_text("[]", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    _install_stub_rclone(bin_dir)

    env = prod_app_env(home)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    # Force the encrypted-at-rest fallback so the credential round-trip is
    # deterministic (no OS keychain prompt) on any host.
    env["PYTHON_KEYRING_BACKEND"] = "keyring.backends.fail.Keyring"
    env["EXLAB_WIZARD_SECRET_PASSPHRASE"] = "e2e-test-passphrase"

    _seed_config(home, env=env, local_root=local_root, catalogue=catalogue)

    port = free_port()
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
        yield base_url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def _setup_state(base_url: str) -> str:
    return httpx.get(f"{base_url}/api/v1/setup/status", timeout=5.0).json()["state"]


def test_nas_credential_gate_set_test_and_clear(browser, nas_prod_server) -> None:
    base_url = nas_prod_server

    # 1. Seeded config has a nas-mode equipment but no keyring password ->
    #    the §4.9 gate reports the new state.
    assert _setup_state(base_url) == "incomplete_no_nas_credential"

    context = browser.new_context()
    page = context.new_page()
    try:
        # 2. Open Settings and switch to the NAS Credentials section.
        page.goto(f"{base_url}/settings")
        page.wait_for_load_state("networkidle")
        page.get_by_test_id("settings-dialog").wait_for(state="visible", timeout=10_000)
        page.get_by_test_id("settings-nav-nas_credentials").click()
        page.get_by_test_id("settings-nas-credential-row-EQ1").wait_for(
            state="visible", timeout=5_000
        )

        # 3. Set the password for EQ1.
        page.get_by_test_id("settings-nas-password-EQ1-primary").click()
        page.get_by_test_id("settings-nas-password-EQ1-input").wait_for(
            state="visible", timeout=5_000
        )
        page.get_by_test_id("settings-nas-password-EQ1-input").fill("nas-secret")
        page.get_by_test_id("settings-nas-password-EQ1-save").click()
        page.wait_for_timeout(500)

        # 4. The credential gate is now satisfied -> READY.
        assert _setup_state(base_url) == "ready"

        # 5. Test connection runs the rclone probe (obscure + about) against
        #    the on-PATH stub and reports success inline.
        page.get_by_test_id("settings-nas-test-EQ1").click()
        page.get_by_text("Connected").wait_for(state="visible", timeout=10_000)

        # 6. Clearing the password reverts the gate.
        page.get_by_test_id("settings-nas-password-EQ1-secondary").click()
        page.get_by_test_id("settings-nas-password-EQ1-clear-confirm").wait_for(
            state="visible", timeout=5_000
        )
        page.get_by_test_id("settings-nas-password-EQ1-clear-confirm").click()
        page.wait_for_timeout(500)

        assert _setup_state(base_url) == "incomplete_no_nas_credential"
    finally:
        context.close()
