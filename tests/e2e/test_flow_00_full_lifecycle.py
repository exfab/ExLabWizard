"""E2E flow 00b: the full create-lifecycle against the PRODUCTION app.

Boots the genuine production wizard (``_build_default_app``) with
``HOME`` redirected to a fresh tmp dir -- a true no-config install --
and drives, end to end, every create flow the wizard exposes:

    fresh install
      -> welcome -> settings (paths + LIMS + add equipment) -> Save
      -> RESTART (config.yaml now drives the controller)
      -> create a project template
      -> create a run template (experimental)
      -> create a run template (test scope)
      -> New Project wizard            -> project dir on disk
      -> New Run wizard (experimental) -> Run_* dir on disk
      -> New Test Run wizard           -> TestRuns/TestRun_* dir on disk

"Loading from a template" is exercised implicitly and explicitly: each
wizard's template picker is populated by scanning the templates dir,
and the test asserts the created templates appear as options.

Every folder (config, state, templates, data) lives under the test's
tmp tree -- nothing touches the real machine.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.e2e._prod_server import ProdServer
from tests.e2e.conftest import PLAYWRIGHT_AVAILABLE

pytestmark = pytest.mark.skipif(
    not PLAYWRIGHT_AVAILABLE,
    reason="playwright not installed",
)


# ---------------------------------------------------------------------------
# Playwright helpers
# ---------------------------------------------------------------------------


def _fill(page, testid: str, value: str, *, timeout: int = 8_000) -> None:
    """Fill a (visible) Quasar input identified by ``data-testid``."""
    field = page.get_by_test_id(testid)
    field.wait_for(state="visible", timeout=timeout)
    field.fill(value)


def _select(page, testid: str, value: str, *, timeout: int = 8_000) -> None:
    """Pick ``value`` from a NiceGUI/Quasar ``ui.select`` by ``data-testid``.

    NiceGUI lands the ``data-testid`` on the select's inner
    ``q-field__native`` div, which is zero-size (and so "not visible"
    to Playwright) while the select is empty. Click the enclosing
    ``q-select`` ancestor to open the dropdown, then pick the option.
    """
    native = page.get_by_test_id(testid)
    native.wait_for(state="attached", timeout=timeout)
    select = native.locator(
        "xpath=ancestor-or-self::*[contains(concat(' ', normalize-space(@class), ' '),"
        " ' q-select ')][1]"
    )
    select.click()
    option = page.get_by_role("option", name=value, exact=True)
    option.wait_for(state="visible", timeout=timeout)
    option.click()


def _step_button(
    page,
    step_testid: str,
    button_testid: str,
    *,
    timeout: int = 8_000,
) -> None:
    """Click a stepper-navigation button scoped to its step container.

    The Next / Create buttons share one ``data-testid`` across steps, so
    they must be scoped by the active step's ``wizard-step-*`` container
    (the same pattern the other flow tests use).
    """
    locator = page.locator(
        f'[data-testid="{step_testid}"] [data-testid="{button_testid}"]'
    )
    locator.wait_for(state="visible", timeout=timeout)
    locator.click()


def _project_next(page, step_id: str) -> None:
    """Advance the project wizard from ``step_id`` via its Next button."""
    _step_button(page, f"wizard-step-{step_id}", "wizard-next")


def _run_next(page, step_id: str) -> None:
    """Advance the run wizard from ``step_id`` via its Next button."""
    _step_button(page, f"wizard-run-step-{step_id}", "wizard-run-next")


def _goto(page, url: str, *, retries: int = 2) -> None:
    """Navigate to a NiceGUI page, tolerating a transient ERR_ABORTED.

    NiceGUI's client occasionally aborts the first document request when
    it issues its connect-time reload handshake; a single retry settles
    it.
    """
    last_error: Exception | None = None
    for _ in range(retries + 1):
        try:
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle")
            return
        except Exception as exc:  # noqa: BLE001 -- retry transient nav aborts
            last_error = exc
            page.wait_for_timeout(300)
    raise AssertionError(f"navigation to {url} failed: {last_error!r}")


# ---------------------------------------------------------------------------
# Fixture: a fresh production server with a redirected HOME
# ---------------------------------------------------------------------------


@pytest.fixture
def prod_server(tmp_path: Path):
    """Yield a started :class:`ProdServer` rooted at a fresh tmp HOME."""
    home = tmp_path / "home"
    home.mkdir()
    server = ProdServer(home)
    if not server.start():
        pytest.skip("production wizard app did not become healthy within 30s")
    try:
        yield server
    finally:
        server.stop()


# ---------------------------------------------------------------------------
# The lifecycle test
# ---------------------------------------------------------------------------


def test_full_create_lifecycle(browser, prod_server: ProdServer, tmp_path: Path) -> None:
    server = prod_server
    config_path = server.config_path
    assert not config_path.exists(), "precondition: fresh install has no config.yaml"

    # Operator-facing folders -- all under the test's tmp tree.
    templates_dir = tmp_path / "templates"
    plugin_dir = tmp_path / "plugins"
    data_root = tmp_path / "data"
    for folder in (templates_dir, plugin_dir, data_root):
        folder.mkdir()

    context = browser.new_context()
    page = context.new_page()
    try:
        # ---- Phase 1: fresh install -> welcome -------------------------
        _goto(page, f"{server.base_url}/")
        page.get_by_test_id("welcome-card").wait_for(state="visible", timeout=10_000)

        # ---- Phase 2: welcome -> settings ------------------------------
        page.get_by_test_id("welcome-get-started").click()
        page.wait_for_load_state("networkidle")
        page.get_by_test_id("settings-dialog").wait_for(state="visible", timeout=10_000)

        # ---- Phase 3: fill paths + LIMS --------------------------------
        page.get_by_test_id("settings-nav-paths").click()
        _fill(page, "settings-paths-templates", str(templates_dir))
        _fill(page, "settings-paths-plugin", str(plugin_dir))
        _fill(page, "settings-paths-local-root", str(data_root))

        page.get_by_test_id("settings-nav-lims").click()
        _fill(page, "settings-lims-endpoint", "https://lims.example.test")
        _fill(page, "settings-lims-email", "operator@example.test")

        # ---- Phase 4: add an equipment ---------------------------------
        page.get_by_test_id("settings-nav-equipment").click()
        _fill(page, "settings-equipment-id", "MICROSCOPE1")
        _fill(page, "settings-equipment-label", "Confocal Microscope 1")
        _fill(page, "settings-equipment-local-root", str(data_root))
        _fill(page, "settings-equipment-nas-root", "/srv/nas/microscope1")
        _fill(page, "settings-equipment-sentinel", "acquisition_complete.flag")
        _fill(page, "settings-equipment-rclone-remote", "lab-nas")
        _fill(page, "settings-equipment-rclone-path", "lab/microscope1")
        page.get_by_test_id("settings-equipment-add").click()
        page.get_by_test_id("settings-equipment-row").wait_for(state="visible", timeout=8_000)

        # ---- Phase 5: save -> restart-required gate --------------------
        page.get_by_test_id("settings-save").click()
        page.wait_for_load_state("networkidle")
        page.get_by_test_id("restart-required").wait_for(state="visible", timeout=10_000)
        assert config_path.exists(), "Save must persist config.yaml"
        config_text = config_path.read_text(encoding="utf-8")
        assert "MICROSCOPE1" in config_text
        assert str(data_root) in config_text

        # ---- Phase 6: restart so the controller picks up the config ----
        assert server.restart(), "production app failed to come back up after restart"
        # The old page holds a websocket to the now-dead server; a fresh
        # page avoids the stale NiceGUI client racing the new boot.
        page.close()
        page = context.new_page()

        # ---- Phase 7: create a project template ------------------------
        _goto(page, f"{server.base_url}/templates")
        page.get_by_test_id("templates-card").wait_for(state="visible", timeout=10_000)
        _fill(page, "template-name", "proj_basic")
        _select(page, "template-type", "project")
        _fill(page, "template-description", "Basic project scaffold")
        page.get_by_test_id("template-create").click()
        page.wait_for_load_state("networkidle")
        # The new template directory exists on disk and is a valid scaffold.
        assert (templates_dir / "proj_basic" / "copier.yml").is_file()
        # 'Back' returns the operator to the main view.
        _goto(page, f"{server.base_url}/templates")
        page.get_by_test_id("templates-back").click()
        page.wait_for_url(re.compile(r".*/main"), timeout=10_000)

        # ---- Phase 8: create run templates (experimental + test) -------
        _goto(page, f"{server.base_url}/templates")
        _fill(page, "template-name", "run_exp")
        _select(page, "template-type", "run")
        _select(page, "template-run-scope", "experimental")
        page.get_by_test_id("template-create").click()
        page.wait_for_load_state("networkidle")
        assert (templates_dir / "run_exp" / "copier.yml").is_file()

        _goto(page, f"{server.base_url}/templates")
        _fill(page, "template-name", "run_test")
        _select(page, "template-type", "run")
        _select(page, "template-run-scope", "test")
        page.get_by_test_id("template-create").click()
        page.wait_for_load_state("networkidle")
        assert (templates_dir / "run_test" / "copier.yml").is_file()

        # ---- Phase 9: New Project wizard -------------------------------
        _goto(page, f"{server.base_url}/wizard/project")
        page.get_by_test_id("wizard-project-card").wait_for(state="visible", timeout=10_000)
        # Step: LIMS project.
        _fill(page, "wizard-project-lims-id", "PROJ-1001")
        _fill(page, "wizard-project-lims-name", "Cortex Mapping Study")
        _project_next(page, "lims_project")
        # Step: template -- the picker is "loading from a template".
        _select(page, "wizard-project-template", "proj_basic")
        _project_next(page, "template")
        # Step: equipment.
        _select(page, "wizard-project-equipment", "MICROSCOPE1")
        _project_next(page, "equipment")
        # Step: variables (template defaults; nothing to enter).
        _project_next(page, "variables")
        # Step: README core fields.
        _fill(page, "wizard-project-readme-label", "Cortex pilot")
        _fill(page, "wizard-project-readme-operator", "operator@example.test")
        _fill(page, "wizard-project-readme-objective", "First-pass cortex calibration.")
        _project_next(page, "readme")
        # Step: preview.
        _project_next(page, "preview")
        # Step: confirm -> Create.
        _step_button(page, "wizard-step-confirm", "wizard-submit")
        page.wait_for_url(re.compile(r".*/main"), timeout=15_000)
        project_dir = data_root / "MICROSCOPE1" / "PROJ-1001"
        assert project_dir.is_dir(), f"project dir not created: {project_dir}"
        assert (project_dir / ".exlab-wizard" / "creation.json").is_file()

        # ---- Phase 10: New Run wizard (experimental) -------------------
        _goto(page, f"{server.base_url}/wizard/run")
        page.get_by_test_id("wizard-run-card-experimental").wait_for(
            state="visible", timeout=10_000
        )
        _fill(page, "wizard-run-project-id", "PROJ-1001")
        _select(page, "wizard-run-equipment", "MICROSCOPE1")
        _run_next(page, "project_equipment")
        _select(page, "wizard-run-template", "run_exp")
        _run_next(page, "template")
        _run_next(page, "variables")
        _fill(page, "wizard-run-readme-label", "Calibration sweep")
        _fill(page, "wizard-run-readme-operator", "operator@example.test")
        _fill(page, "wizard-run-readme-objective", "Sweep laser wavelengths.")
        _run_next(page, "readme")
        _run_next(page, "preview")
        _step_button(page, "wizard-run-step-confirm", "wizard-run-submit")
        page.wait_for_url(re.compile(r".*/main"), timeout=15_000)
        run_dirs = list((project_dir).glob("Run_*"))
        assert run_dirs, f"experimental run dir not created under {project_dir}"
        assert (run_dirs[0] / ".exlab-wizard" / "creation.json").is_file()

        # ---- Phase 11: New Test Run wizard -----------------------------
        _goto(page, f"{server.base_url}/wizard/test-run")
        page.get_by_test_id("wizard-run-card-test").wait_for(state="visible", timeout=10_000)
        _fill(page, "wizard-run-project-id", "PROJ-1001")
        _select(page, "wizard-run-equipment", "MICROSCOPE1")
        _run_next(page, "project_equipment")
        _select(page, "wizard-run-template", "run_test")
        _run_next(page, "template")
        _run_next(page, "variables")
        _fill(page, "wizard-run-readme-label", "Dry run")
        _fill(page, "wizard-run-readme-operator", "operator@example.test")
        _fill(page, "wizard-run-readme-objective", "Dry-run the acquisition pipeline.")
        _run_next(page, "readme")
        _run_next(page, "preview")
        _step_button(page, "wizard-run-step-confirm", "wizard-run-submit")
        page.wait_for_url(re.compile(r".*/main"), timeout=15_000)
        test_run_dirs = list((project_dir / "TestRuns").glob("TestRun_*"))
        assert test_run_dirs, f"test run dir not created under {project_dir / 'TestRuns'}"
        assert (test_run_dirs[0] / ".exlab-wizard" / "creation.json").is_file()
    finally:
        context.close()
