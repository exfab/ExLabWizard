"""pystray icon construction. Backend Spec §4.3.2.

The tray icon's menu has three top-level items per the §4.1 contract:

* **Open** -- spawns or focuses the window subprocess (the launcher
  routes this to :class:`WindowLauncher.open`).
* **Status** submenu -- live state (formatted by :mod:`tray.status`)
  refreshed every 5 seconds.
* **Quit** -- triggers the graceful-shutdown protocol (the launcher
  routes this to :class:`QuitCoordinator.quit`).

The icon image is a small in-memory PIL bitmap. We don't ship a real
icon file at this phase; the asset hookup lives with PyInstaller's
``--add-data`` config in §15.1. The runtime image is generated from
:func:`_default_icon_image` -- a 64x64 navy square with a centered "E"
glyph -- which keeps the runtime self-contained and avoids relying on
file-system paths during PyInstaller bundling.

Tests pass an injected ``pystray`` module (or a stand-in) via the
``pystray_module`` parameter so they can build the menu graph without
the real backend.
"""

from __future__ import annotations

import io
from collections.abc import Callable
from typing import Any

from exlab_wizard.logging import get_logger

__all__ = ["DEFAULT_ICON_NAME", "build_icon", "default_icon_image"]

_log = get_logger(__name__)

DEFAULT_ICON_NAME = "exlab-wizard"


def default_icon_image() -> Any:
    """Return a small PIL.Image used as the tray icon bitmap.

    Imported lazily so the rest of the module is import-safe even when
    Pillow is not yet installed (ie. minimal CI runs). On failure
    returns a one-byte ``bytes`` object the caller may pass straight
    to pystray; pystray accepts file-like objects too.
    """
    try:
        from PIL import Image, ImageDraw

        img = Image.new("RGBA", (64, 64), (10, 30, 70, 255))
        draw = ImageDraw.Draw(img)
        draw.text((20, 18), "E", fill=(255, 255, 255, 255))
        return img
    except Exception:  # pragma: no cover -- Pillow rarely missing
        return io.BytesIO(b"\x00")


def build_icon(
    *,
    on_open: Callable[[], None],
    on_quit: Callable[[], None],
    status_provider: Callable[[], str],
    pystray_module: Any = None,
    icon_image: Any = None,
    icon_name: str = DEFAULT_ICON_NAME,
    title: str = "ExLab-Wizard",
) -> Any:
    """Build the pystray icon with the §4.1 menu.

    ``status_provider`` is invoked every time pystray re-renders the
    menu (pystray supports lazily-evaluated text via callable labels)
    so the operator sees live status without a separate refresh
    notification.
    """
    pystray = pystray_module if pystray_module is not None else _import_pystray()
    image = icon_image if icon_image is not None else default_icon_image()

    open_item = pystray.MenuItem(
        "Open",
        lambda _icon=None, _item=None: _safe_call(on_open, "Open"),
        default=True,
    )
    status_item = pystray.MenuItem(
        lambda _item: f"Status: {status_provider()}",
        None,
        enabled=False,
    )
    quit_item = pystray.MenuItem("Quit", lambda _icon=None, _item=None: _safe_call(on_quit, "Quit"))
    menu = pystray.Menu(open_item, status_item, pystray.Menu.SEPARATOR, quit_item)

    return pystray.Icon(icon_name, image, title, menu)


def _import_pystray() -> Any:
    """Lazy import so unit tests on headless hosts can mock pystray."""
    import pystray

    return pystray


def _safe_call(fn: Callable[[], None], label: str) -> None:
    """Invoke a menu callback; log + swallow exceptions.

    The pystray menu handler thread is the same thread as the icon's
    event loop; an unhandled exception there can crash the whole tray.
    Routing every callback through this shim keeps the tray alive even
    when, say, the quit prompt callback raises.
    """
    try:
        fn()
    except Exception:
        _log.exception("tray menu callback failed: %s", label)
