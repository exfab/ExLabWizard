"""Generate Playwright screenshots for the Sphinx user guide.

CLI:

    python scripts/generate_screenshots.py [output_dir]

Default output directory: ``docs/_static/screenshots``.

The script spawns the same uvicorn-hosted e2e test app
(:func:`tests.e2e._test_app.create_app_factory`) that the e2e suite uses,
launches a headless Chromium via Playwright, and captures one or more
PNGs per user-visible capability. The capability map mirrors the
``docs/user_guide/`` tree:

================  ====================================================
Capability id     Source route(s) inside the test app
================  ====================================================
01_create_project ``/wizard/project``
02_create_run     ``/wizard/run``
03_create_test_run ``/wizard/test-run``
04_browse         ``/main``
05_readme         ``/wizard/project`` (README sub-step)
06_settings       ``/settings``
07_orchestrator   ``/staging`` and ``/main?orchestrator=1``
08_problems       ``/problems?seed=hard``
================  ====================================================

If a capability cannot be screenshotted (for example, the test app's
selector did not appear within the timeout), the script logs a WARNING
and continues. Failures are reported in the final summary so the
documentation build can decide whether to defer the corresponding
guide page.

The script must run cleanly on the GitHub Actions ``ubuntu-latest``
runner with chromium installed via ``playwright install chromium``.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path
from typing import Any

import httpx

# The script lives in scripts/, the package in exlab_wizard/, the
# tests in tests/. Ensure the repo root is on sys.path so we can
# import the e2e harness modules without installing them.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.e2e.conftest import _resolve_chromium_executable  # noqa: E402

VIEWPORT = {"width": 1280, "height": 720}
DEFAULT_OUTPUT = REPO_ROOT / "docs" / "_static" / "screenshots"


def _free_port() -> int:
    """Return a free TCP port on 127.0.0.1."""
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _start_server(state_dir: Path) -> tuple[subprocess.Popen[bytes], str]:
    """Spawn the uvicorn-hosted e2e test app; return (proc, base_url).

    Mirrors the lifecycle pattern from tests/e2e/conftest.py:server_url
    (lines 89-151) including the EXLAB_TESTING / EXLAB_STATE_DIR /
    EXLAB_PORT env vars and the /api/v1/health poll.
    """
    port = _free_port()
    env = {
        **os.environ,
        "EXLAB_TESTING": "1",
        "EXLAB_STATE_DIR": str(state_dir),
        "EXLAB_PORT": str(port),
        "PYTHONPATH": str(REPO_ROOT) + os.pathsep + os.environ.get("PYTHONPATH", ""),
    }
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "tests.e2e._test_app:create_app_factory",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        env=env,
    )
    base_url = f"http://127.0.0.1:{port}"
    healthy = False
    for _ in range(60):
        try:
            response = httpx.get(f"{base_url}/api/v1/health", timeout=1.0)
            if response.status_code == 200:
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
        raise RuntimeError("uvicorn server did not respond to /api/v1/health within 30s")
    return proc, base_url


def _stop_server(proc: subprocess.Popen[bytes]) -> None:
    """Best-effort termination of the uvicorn subprocess."""
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def _capture(
    page: Any,
    base_url: str,
    route: str,
    output_path: Path,
    *,
    wait_selector: str | None = None,
    full_page: bool = False,
    timeout_ms: int = 10_000,
) -> bool:
    """Navigate to ``route``, optionally wait for a selector, screenshot.

    Returns True on success, False on timeout or navigation error. The
    output directory is created on demand.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        page.goto(f"{base_url}{route}")
        page.wait_for_load_state("networkidle", timeout=timeout_ms)
        if wait_selector:
            page.wait_for_selector(wait_selector, timeout=timeout_ms, state="visible")
        # Small settle delay so any post-load animations finish.
        page.wait_for_timeout(200)
        page.screenshot(path=str(output_path), full_page=full_page)
        return True
    except Exception as exc:
        print(f"  WARN: capture failed for {route} -> {output_path.name}: {exc}")
        return False


def _capture_capability(
    page: Any,
    base_url: str,
    output_root: Path,
    capability_id: str,
    steps: list[dict[str, Any]],
) -> tuple[int, int]:
    """Capture every step in the capability; return (succeeded, total)."""
    succeeded = 0
    total = len(steps)
    for index, step in enumerate(steps):
        step_id = step["step_id"]
        route = step["route"]
        wait_selector = step.get("wait_selector")
        # First screenshot is full page; subsequent ones are viewport-only
        # to keep file sizes small.
        full_page = index == 0
        output_path = output_root / capability_id / f"{step_id}.png"
        ok = _capture(
            page,
            base_url,
            route,
            output_path,
            wait_selector=wait_selector,
            full_page=full_page,
        )
        if ok:
            succeeded += 1
    return succeeded, total


def _capability_plan() -> list[tuple[str, list[dict[str, Any]]]]:
    """Return the (capability_id, steps) list driving the screenshot run.

    Routes and selectors are confirmed against tests/e2e/_test_app.py
    and the page-object modules under tests/e2e/page_objects/.
    """
    return [
        (
            "01_create_project",
            [
                {
                    "step_id": "01_initial",
                    "route": "/wizard/project",
                    "wait_selector": '[data-testid="wizard-project-card"]',
                },
            ],
        ),
        (
            "02_create_run",
            [
                {
                    "step_id": "01_initial",
                    "route": "/wizard/run",
                    "wait_selector": '[data-testid="wizard-run-stepper"]',
                },
            ],
        ),
        (
            "03_create_test_run",
            [
                {
                    "step_id": "01_initial",
                    "route": "/wizard/test-run",
                    "wait_selector": '[data-testid="wizard-run-stepper"]',
                },
            ],
        ),
        (
            "04_browse",
            [
                {
                    "step_id": "01_initial",
                    "route": "/main",
                    "wait_selector": '[data-testid="main-tree"]',
                },
            ],
        ),
        (
            "05_readme",
            [
                # The README form is a sub-step of the project wizard;
                # the test app does not expose a deep-link query
                # parameter, so we capture the wizard's initial render
                # (which surfaces the stepper holding the README step)
                # and document the limitation in the user guide page.
                {
                    "step_id": "01_initial",
                    "route": "/wizard/project",
                    "wait_selector": '[data-testid="wizard-project-card"]',
                },
            ],
        ),
        (
            "06_settings",
            [
                {
                    "step_id": "01_initial",
                    "route": "/settings",
                    "wait_selector": '[data-testid="settings-dialog"]',
                },
            ],
        ),
        (
            "07_orchestrator",
            [
                {
                    "step_id": "01_initial",
                    "route": "/staging",
                    "wait_selector": '[data-testid="staging-dock"]',
                },
                {
                    "step_id": "02_main",
                    "route": "/main?orchestrator=1",
                    "wait_selector": '[data-testid="main-tree"]',
                },
            ],
        ),
        (
            "08_problems",
            [
                {
                    "step_id": "01_initial",
                    "route": "/problems?seed=hard",
                    "wait_selector": '[data-testid="problems-table"]',
                },
            ],
        ),
        # GUI/Orchestrator Redesign capability additions.
        (
            "09_add_equipment_wizard",
            [
                {
                    "step_id": "01_identity",
                    "route": "/wizard/equipment",
                    "wait_selector": '[data-testid="wizard-equipment-id"]',
                },
            ],
        ),
        (
            "10_file_explorer",
            [
                {
                    "step_id": "01_overview",
                    "route": "/main?view=explorer",
                    "wait_selector": '[data-testid="toolbar-add-equipment"]',
                },
            ],
        ),
    ]


def main(argv: list[str] | None = None) -> int:
    """Entry point; returns a process exit code (0 on full success)."""
    args = list(sys.argv[1:] if argv is None else argv)
    output_root = Path(args[0]).resolve() if args else DEFAULT_OUTPUT
    output_root.mkdir(parents=True, exist_ok=True)
    print(f"Output directory: {output_root}")

    # Defer the playwright import so the script's --help (if added later)
    # still works without playwright installed.
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        print(f"ERROR: playwright is not installed: {exc}")
        print("Install with: pip install -e .[test] && playwright install chromium")
        return 2

    chromium_path = _resolve_chromium_executable()
    if chromium_path:
        print(f"Using chromium at: {chromium_path}")
    else:
        print("Using playwright's default chromium resolution")

    # Place the per-run state directory under the docs build tree so a
    # local run does not litter the repo root with a top-level
    # ``.docs_screenshot_state/`` folder. The docs/_build/ tree is
    # already gitignored.
    state_dir = REPO_ROOT / "docs" / "_build" / "screenshot_state"
    state_dir.mkdir(parents=True, exist_ok=True)
    proc, base_url = _start_server(state_dir)
    print(f"Server running at: {base_url}")

    summary: list[tuple[str, int, int]] = []
    try:
        with sync_playwright() as pw:
            launch_kwargs: dict[str, Any] = {"headless": True}
            if chromium_path:
                launch_kwargs["executable_path"] = chromium_path
            browser = pw.chromium.launch(**launch_kwargs)
            try:
                context = browser.new_context(viewport=VIEWPORT)
                page = context.new_page()
                for capability_id, steps in _capability_plan():
                    print(f"Capturing capability: {capability_id} ({len(steps)} step(s))")
                    succeeded, total = _capture_capability(
                        page, base_url, output_root, capability_id, steps
                    )
                    summary.append((capability_id, succeeded, total))
                context.close()
            finally:
                browser.close()
    finally:
        _stop_server(proc)

    print("\nSummary:")
    full_success = True
    for capability_id, succeeded, total in summary:
        status = "OK" if succeeded == total else "PARTIAL"
        print(f"  {capability_id}: {succeeded}/{total} {status}")
        if succeeded == 0 and total > 0:
            full_success = False

    # The script is permissive: a partial capture is acceptable, but a
    # capability with zero successful captures is treated as a failure
    # (the docs page would have no images to render).
    return 0 if full_success else 1


if __name__ == "__main__":
    raise SystemExit(main())
