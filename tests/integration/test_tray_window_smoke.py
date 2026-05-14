"""Integration smoke: pywebview-facing root path returns HTML, not JSON 404.

This regression test pins the diagnosis that motivated wiring the
NiceGUI wizard onto the FastAPI app: before the fix, booting the tray
produced a working ``/api/v1/health`` but a ``404 {"detail":"Not Found"}``
at ``/``. We boot the production tray app on a tmp state dir, hit the
root path, and assert we get an HTML response. If NiceGUI's runtime
cannot start in the current environment, the test is skipped rather
than failing -- this lets the test live alongside other integration
tests on a headless CI runner.
"""

from __future__ import annotations

import time
from pathlib import Path

import httpx
import pytest

from exlab_wizard.tray.server_runner import ServerRunner


def _wait_for(port: int, path: str, *, timeout: float = 5.0) -> httpx.Response:
    deadline = time.monotonic() + timeout
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with httpx.Client(timeout=1.0) as client:
                resp = client.get(f"http://127.0.0.1:{port}{path}")
                if resp.status_code != 502:
                    return resp
        except Exception as exc:
            last_exc = exc
            time.sleep(0.05)
    msg = f"server did not respond on {path}: {last_exc!r}"
    raise AssertionError(msg)


def test_root_serves_html_after_mount(tmp_path: Path) -> None:
    try:
        from exlab_wizard.tray.main import _build_default_app

        app = _build_default_app(tmp_path)
    except Exception as exc:
        pytest.skip(f"NiceGUI stack unavailable: {exc}")

    runner = ServerRunner(app=app, state_dir=tmp_path)
    port = runner.start()
    try:
        health = _wait_for(port, "/api/v1/health")
        assert health.status_code == 200, health.text

        root = _wait_for(port, "/")
        assert root.status_code in (200, 303, 307), (
            f"root path returned {root.status_code}; body: {root.text[:200]!r}"
        )
        content_type = root.headers.get("content-type", "")
        assert "html" in content_type or root.status_code in (303, 307), (
            f"expected HTML at /, got content-type={content_type!r} body={root.text[:200]!r}"
        )
    finally:
        runner.stop()
