"""pywebview-driven native window. Backend Spec §15.3.2.

Opens a single pywebview window pointed at
``http://127.0.0.1:<port>`` from the handshake. Window title, size, and
icon are hard-coded here per §15.3.2; devtools are gated by the
``EXLAB_DEBUG`` env var so release artifacts never enable them.

The actual ``webview`` import is deferred and isolated behind helper
functions so unit tests on a headless host can exercise URL building,
debug flag detection, and the assembly path without booting a real
webview backend.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from exlab_wizard.logging import get_logger

if TYPE_CHECKING:
    from exlab_wizard.window.main import ServerHandshake

__all__ = [
    "DEFAULT_HEIGHT",
    "DEFAULT_WIDTH",
    "WINDOW_TITLE",
    "build_window_url",
    "is_debug_enabled",
    "run_window",
]

_log = get_logger(__name__)

WINDOW_TITLE = "ExLab-Wizard"
DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 800
DEBUG_ENV_VAR = "EXLAB_DEBUG"


def build_window_url(handshake: ServerHandshake) -> str:
    """Return the URL the window points at.

    Always loopback (Backend §4.1: "binds to ``127.0.0.1`` only"); the
    handshake's port is mandatory.
    """
    return f"http://127.0.0.1:{int(handshake.port)}"


def is_debug_enabled() -> bool:
    """True when ``EXLAB_DEBUG`` is set to a truthy string. Backend §15.3.2."""
    raw = os.environ.get(DEBUG_ENV_VAR, "")
    if not raw:
        return False
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def run_window(
    handshake: ServerHandshake,
    *,
    create_window: Callable[..., Any] | None = None,
    start: Callable[..., Any] | None = None,
) -> int:
    """Open a single pywebview window pointed at the handshake's port.

    ``create_window`` and ``start`` are dependency-injection hooks for
    tests; production code defers to ``webview.create_window`` and
    ``webview.start``. Returns 0 on clean exit.
    """
    url = build_window_url(handshake)
    debug = is_debug_enabled()
    _log.info("opening window at %s (debug=%s)", url, debug)

    if create_window is None or start is None:
        webview = _import_webview()
        if create_window is None:
            create_window = webview.create_window
        if start is None:
            start = webview.start

    create_window(
        title=WINDOW_TITLE,
        url=url,
        width=DEFAULT_WIDTH,
        height=DEFAULT_HEIGHT,
        resizable=True,
    )
    start(debug=debug)
    return 0


def _import_webview() -> Any:
    """Lazily import pywebview to keep the module import cheap."""
    import webview

    return webview
