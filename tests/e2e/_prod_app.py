"""uvicorn ``--factory`` entrypoint for the *production* wizard app.

``_test_app.py`` mounts a ``TestState``-backed surface for the flow
tests. This module instead boots the real production app -- the same
``exlab_wizard.tray._build_default_app`` the tray uses -- so a
Playwright test can drive the genuine welcome -> settings -> save ->
restart-required flow built by ``exlab_wizard.ui.mount``.

The e2e test spawns uvicorn against this factory with ``HOME`` pointed
at a fresh tmp directory, so ``paths.os_config_path`` /
``paths.os_state_path`` resolve under tmp: a true fresh install with no
``config.yaml`` and every state/cache/log folder relocated to tmp.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI


def create_prod_app_factory() -> FastAPI:
    """Build the production wizard app (NiceGUI mounted at ``/``)."""
    from exlab_wizard.paths import ensure_state_dir
    from exlab_wizard.tray.main import _build_default_app

    return _build_default_app(ensure_state_dir())
