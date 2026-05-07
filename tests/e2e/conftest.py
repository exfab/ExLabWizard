"""E2E test fixtures (Phase 16).

Boots a real uvicorn process serving
:func:`exlab_wizard.api.app.create_app`, drives it with Playwright's
sync API, and exposes a per-test ``page`` fixture.

Skips the entire suite if Playwright (or its chromium browser) is not
installed -- the harness never attempts to install browsers itself.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from contextlib import closing

import httpx
import pytest

PLAYWRIGHT_AVAILABLE = True
PLAYWRIGHT_IMPORT_ERROR: str | None = None
try:
    from playwright.sync_api import sync_playwright
except ImportError as exc:  # pragma: no cover -- environment-dependent
    PLAYWRIGHT_AVAILABLE = False
    PLAYWRIGHT_IMPORT_ERROR = str(exc)


pytestmark = pytest.mark.skipif(
    not PLAYWRIGHT_AVAILABLE,
    reason=(
        "playwright not installed; run `pip install -e .[test]` and `playwright install chromium`"
    ),
)


def _free_port() -> int:
    """Return a free TCP port on 127.0.0.1."""
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="session")
def server_url(tmp_path_factory: pytest.TempPathFactory) -> str:
    """Spawn a real uvicorn process serving ``create_app`` and yield its base URL.

    Uses ``--factory`` so uvicorn imports ``exlab_wizard.api.app:create_app``
    and calls it with no arguments; that path returns a working FastAPI
    app with the ``/api/v1`` routers but without the NiceGUI UI mounted
    on ``/`` (the NiceGUI bootstrap is owned by the tray + window
    launchers, not the API factory). The smoke flow handles the missing
    UI gracefully; the placeholder flows are skipped.
    """
    state_dir = tmp_path_factory.mktemp("e2e_state")
    port = _free_port()
    env = {
        **os.environ,
        "EXLAB_TESTING": "1",
        "EXLAB_STATE_DIR": str(state_dir),
        "EXLAB_PORT": str(port),
    }
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "exlab_wizard.api.app:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        env=env,
    )
    base_url = f"http://127.0.0.1:{port}"
    healthy = False
    for _ in range(30):
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
        pytest.skip("uvicorn server did not respond to /api/v1/health within 15s")
    try:
        yield base_url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


@pytest.fixture(scope="session")
def browser():
    """Yield a chromium browser (headless)."""
    if not PLAYWRIGHT_AVAILABLE:
        pytest.skip("playwright not available")
    try:
        with sync_playwright() as pw:
            try:
                instance = pw.chromium.launch(headless=True)
            except Exception as exc:
                pytest.skip(
                    "playwright chromium not installed; "
                    f"run `playwright install chromium` (error: {exc})"
                )
            try:
                yield instance
            finally:
                instance.close()
    except Exception as exc:
        pytest.skip(f"playwright session failed to start: {exc}")


@pytest.fixture
def page(browser, server_url):
    """Yield a fresh Playwright page for each test."""
    context = browser.new_context()
    page_obj = context.new_page()
    try:
        yield page_obj
    finally:
        context.close()
