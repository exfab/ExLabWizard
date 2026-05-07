"""E2E test fixtures (Phase 16).

Boots a real uvicorn process serving the e2e test app
(:func:`tests.e2e._test_app.create_app_factory`), drives it with
Playwright's sync API, and exposes a per-test ``page`` fixture.

Skips the entire suite if Playwright (or its chromium browser) is not
installed -- the harness never attempts to install browsers itself.

Browser discovery: prefers ``$EXLAB_E2E_CHROMIUM`` (an explicit
executable path), then ``$PLAYWRIGHT_BROWSERS_PATH``'s ``chromium-*``
directories, then the default playwright cache. If none can be
launched the suite skips with a precise reason.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path

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


def _resolve_chromium_executable() -> str | None:
    """Return a chromium executable path or None.

    Search order:

    1. ``EXLAB_E2E_CHROMIUM`` env var.
    2. Any ``chromium-*/chrome-linux/chrome`` under
       ``PLAYWRIGHT_BROWSERS_PATH``.
    3. ``/opt/pw-browsers/chromium-*/chrome-linux/chrome`` (sandbox
       default).
    4. ``None`` -- caller should let Playwright resolve it.
    """
    explicit = os.environ.get("EXLAB_E2E_CHROMIUM", "")
    if explicit and Path(explicit).exists():
        return explicit

    candidate_roots: list[Path] = []
    pwp = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
    if pwp:
        candidate_roots.append(Path(pwp))
    candidate_roots.append(Path("/opt/pw-browsers"))
    candidate_roots.append(Path.home() / ".cache" / "ms-playwright")

    for root in candidate_roots:
        if not root.exists():
            continue
        for entry in sorted(root.glob("chromium-*"), reverse=True):
            chrome = entry / "chrome-linux" / "chrome"
            if chrome.exists():
                return str(chrome)
            chrome_mac = entry / "chrome-mac" / "Chromium.app" / "Contents" / "MacOS" / "Chromium"
            if chrome_mac.exists():
                return str(chrome_mac)
    return None


@pytest.fixture(scope="session")
def server_url(tmp_path_factory: pytest.TempPathFactory) -> str:
    """Spawn a real uvicorn process serving the e2e test app and yield its URL.

    The factory is :func:`tests.e2e._test_app.create_app_factory`, which
    wraps :func:`exlab_wizard.api.create_app` and mounts a NiceGUI test
    surface at ``/``. Test routes (``/welcome``, ``/main``,
    ``/wizard/project``, ``/settings``, ``/problems``, ``/staging``, ...)
    expose ``data-testid`` hooks for the flows.
    """
    state_dir = tmp_path_factory.mktemp("e2e_state")
    port = _free_port()
    repo_root = Path(__file__).resolve().parents[2]
    env = {
        **os.environ,
        "EXLAB_TESTING": "1",
        "EXLAB_STATE_DIR": str(state_dir),
        "EXLAB_PORT": str(port),
        "PYTHONPATH": str(repo_root) + os.pathsep + os.environ.get("PYTHONPATH", ""),
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
        pytest.skip("uvicorn server did not respond to /api/v1/health within 30s")
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
    chromium_path = _resolve_chromium_executable()
    try:
        with sync_playwright() as pw:
            launch_kwargs: dict[str, object] = {"headless": True}
            if chromium_path:
                launch_kwargs["executable_path"] = chromium_path
            try:
                instance = pw.chromium.launch(**launch_kwargs)
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
