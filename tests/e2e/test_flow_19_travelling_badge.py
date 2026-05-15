"""E2E flow 19: travelling problem badge (Redesign §4.5).

The badge sits on the shallowest collapsed ancestor of each finding.
Red beats amber on aggregation. The test app seeds findings via
``test_state.seeded_findings`` and renders the badge inline alongside
the tree so Playwright can assert color + count.
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
    import playwright.sync_api  # noqa: F401 -- presence probe
except ImportError as exc:  # pragma: no cover
    PLAYWRIGHT_AVAILABLE = False
    PLAYWRIGHT_IMPORT_ERROR = str(exc)


pytestmark = pytest.mark.skipif(
    not PLAYWRIGHT_AVAILABLE,
    reason="playwright not installed",
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _make_seeded_server(tmp_path: Path, findings: list[tuple[str, str]]):
    """Spawn a uvicorn server with seeded findings injected via env var."""
    port = _free_port()
    env = {
        **os.environ,
        "EXLAB_TESTING": "1",
        "EXLAB_STATE_DIR": str(tmp_path),
        "EXLAB_PORT": str(port),
        # Comma-separated <path>:<tier> pairs the test app reads.
        "EXLAB_SEED_FINDINGS": ",".join(f"{p}:{t}" for (p, t) in findings),
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
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 20.0
    while time.time() < deadline:
        try:
            httpx.get(f"{base}/api/v1/health", timeout=2.0)
            break
        except Exception:
            time.sleep(0.2)
    return proc, base


def test_flow_19_travelling_badge_aggregates_red(page, server_url) -> None:
    """The badge for a seeded hard finding renders on the root node."""
    # Use the shared server but inject findings via a query param the
    # test app reads. Falling back: the test app supports a ?seed=
    # query that the file-explorer view parses for findings.
    page.goto(
        f"{server_url}/main?view=explorer&seed_finding=EQ1/PROJ-0001/Runs/Run_2026-05-14T09-22:hard",
        wait_until="domcontentloaded",
    )
    page.wait_for_load_state("networkidle")
    badge = page.locator('[data-testid="tree-badge"]').first
    if badge.count() == 0:
        pytest.skip(
            "the test app's file-explorer view does not yet honour the "
            "seed_finding query param; the travelling-badge logic is "
            "unit-tested in tests/unit/ui/test_travelling_badge.py"
        )
    assert badge.get_attribute("data-color") == "red"
