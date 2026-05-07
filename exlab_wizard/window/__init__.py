"""Window application package. Backend Spec §4.3.2.

Public API:

* :func:`run_window` -- open a pywebview window at the handshake's port.
* :func:`build_window_url` -- compose the loopback URL.
* :class:`ServerHandshake` -- the validated ``server.json`` payload.

We deliberately avoid re-exporting the ``main()`` function at the
package root to keep ``exlab_wizard.window.main`` resolving to the
submodule (the launcher imports the submodule, not the function).
"""

from exlab_wizard.window.main import (
    EXIT_OK,
    EXIT_STALE_STATE,
    ServerHandshake,
    is_pid_alive,
    read_server_handshake,
)
from exlab_wizard.window.pywebview_app import (
    WINDOW_TITLE,
    build_window_url,
    is_debug_enabled,
    run_window,
)

__all__ = [
    "EXIT_OK",
    "EXIT_STALE_STATE",
    "WINDOW_TITLE",
    "ServerHandshake",
    "build_window_url",
    "is_debug_enabled",
    "is_pid_alive",
    "read_server_handshake",
    "run_window",
]
