"""Tray application package. Backend Spec §4.3.2.

Public API:

* :class:`AutostartManager` -- per-platform autostart register /
  unregister / is_registered.
* :class:`NotificationBus` -- coalesced OS notifications via plyer.
* :class:`QuitCoordinator` -- §4.3.2 graceful-shutdown protocol.
* :class:`ServerRunner` -- in-process uvicorn host with atomic
  ``server.json`` writes.
* :class:`StatusTicker` -- 5-second status submenu refresh.
* :class:`TrayApp` -- the long-lived wiring assembly used by
  :func:`exlab_wizard.tray.main`.
* :class:`WindowLauncher` -- spawns / focuses
  ``exlab-wizard-window`` subprocess.
* :func:`build_icon` -- pystray icon factory.
"""

from exlab_wizard.tray.autostart import AutostartManager
from exlab_wizard.tray.icon import build_icon
from exlab_wizard.tray.main import TrayApp
from exlab_wizard.tray.notifications import NotificationBus, notify
from exlab_wizard.tray.quit_coordinator import QuitCoordinator
from exlab_wizard.tray.server_runner import ServerRunner
from exlab_wizard.tray.status import (
    StatusSnapshot,
    StatusTicker,
    format_status,
    snapshot_status,
)
from exlab_wizard.tray.window_launcher import WindowLauncher

__all__ = [
    "AutostartManager",
    "NotificationBus",
    "QuitCoordinator",
    "ServerRunner",
    "StatusSnapshot",
    "StatusTicker",
    "TrayApp",
    "WindowLauncher",
    "build_icon",
    "format_status",
    "notify",
    "snapshot_status",
]
