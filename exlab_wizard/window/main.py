"""``exlab-wizard-window`` console_scripts entry point. Backend Spec §15.3.2.

Reads ``<state_dir>/server.json`` written by the tray's
:class:`ServerRunner`, validates the recorded PID is alive, and hands
off to :mod:`pywebview_app`.

Stale state file -- prints a one-line message to stderr and exits with
status 2 (the tray's :class:`WindowLauncher` interprets this as "tray
died; restart from scratch"; Backend §4.3.2).
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path

from exlab_wizard.logging import get_logger

__all__ = [
    "EXIT_OK",
    "EXIT_STALE_STATE",
    "EXIT_USAGE",
    "ServerHandshake",
    "main",
    "read_server_handshake",
]

_log = get_logger(__name__)

EXIT_OK = 0
EXIT_STALE_STATE = 2
EXIT_USAGE = 64


class ServerHandshake:
    """Resolved ``server.json`` contents: ``port`` / ``pid`` / ``started_at``."""

    __slots__ = ("pid", "port", "started_at")

    def __init__(self, *, port: int, pid: int, started_at: str) -> None:
        self.port = int(port)
        self.pid = int(pid)
        self.started_at = str(started_at)


def read_server_handshake(state_dir: Path) -> ServerHandshake | None:
    """Read and validate ``<state_dir>/server.json``.

    Returns the handshake on success, ``None`` if the file is missing
    or malformed. Validation is intentionally minimal -- if the file
    exists, the PID is alive, and the port parses, we proceed; deeper
    health checks happen on the first HTTP call inside pywebview.
    """
    state_path = Path(state_dir) / "server.json"
    if not state_path.exists():
        return None
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    try:
        return ServerHandshake(
            port=int(payload["port"]),
            pid=int(payload["pid"]),
            started_at=str(payload.get("started_at", "")),
        )
    except (KeyError, TypeError, ValueError):
        return None


def is_pid_alive(pid: int) -> bool:
    """Cross-platform "is this PID a live process?" check.

    Implementation: POSIX ``os.kill(pid, 0)`` raises ``ProcessLookupError``
    if the PID is dead and ``PermissionError`` if it's alive but owned
    by a different uid (counts as alive for our purposes -- if the
    operator started a previous tray that is now owned by another user,
    we still don't want to hijack it). Windows uses ``OpenProcess`` via
    ``ctypes``; we deliberately keep the implementation small and
    inline because it has no other call sites.
    """
    if pid <= 0:
        return False
    if sys.platform == "win32":
        return _win_is_pid_alive(pid)
    return _posix_is_pid_alive(pid)


def _posix_is_pid_alive(pid: int) -> bool:
    import os

    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Different uid; counts as alive (we cannot tell otherwise).
        return True


# Win32 process-state constants (named to match the Microsoft API).
_SYNCHRONIZE = 0x00100000
_STILL_ACTIVE = 259


def _win_is_pid_alive(pid: int) -> bool:  # pragma: no cover -- non-Windows CI
    import ctypes

    handle = ctypes.windll.kernel32.OpenProcess(_SYNCHRONIZE, False, pid)
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        return bool(ok) and exit_code.value == _STILL_ACTIVE
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------


def main(
    argv: list[str] | None = None,
    *,
    state_dir: Path | None = None,
    handoff: Callable[[ServerHandshake], int] | None = None,
    pid_alive: Callable[[int], bool] | None = None,
) -> int:
    """Entry point. Backend Spec §15.3.2.

    ``argv`` is ignored at this phase (the spec lists ``--debug`` as a
    debug-build-only flag; release artifacts read the ``EXLAB_DEBUG``
    env var instead).

    ``state_dir``, ``handoff``, and ``pid_alive`` are dependency
    injection hooks for tests.
    """
    _ = argv
    if state_dir is None:
        from exlab_wizard.paths import ensure_state_dir

        state_dir = ensure_state_dir()
    handshake = read_server_handshake(state_dir)
    if handshake is None:
        sys.stderr.write(
            "exlab-wizard-window: server.json missing or unreadable; is the tray running?\n"
        )
        return EXIT_STALE_STATE

    alive_check = pid_alive if pid_alive is not None else is_pid_alive
    if not alive_check(handshake.pid):
        sys.stderr.write(
            f"exlab-wizard-window: server.json points at dead PID {handshake.pid}; "
            "restart the tray.\n"
        )
        return EXIT_STALE_STATE

    handler = handoff if handoff is not None else _default_handoff
    return handler(handshake)


def _default_handoff(handshake: ServerHandshake) -> int:
    """Hand off control to :mod:`pywebview_app`."""
    from exlab_wizard.window.pywebview_app import run_window

    return run_window(handshake)


if __name__ == "__main__":  # pragma: no cover -- script entrypoint
    sys.exit(main())
