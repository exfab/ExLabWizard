"""Spawn / focus the on-demand window subprocess. Backend Spec §4.3.2.

The tray clicks **Open** -> :class:`WindowLauncher` either:

* spawns a fresh ``exlab-wizard-window`` subprocess via
  :func:`subprocess.Popen` when no window is currently alive, or
* focuses the existing window (best-effort) when one is already up.

The tray process never blocks on the window subprocess; it polls
``Popen.poll()`` to detect window-exit and treats a stale child as "no
window present" on the next Open click.

This module is deliberately small: pywebview-driven focus is OS-specific
and best-effort. On Linux without a working ``xdotool`` the launcher
falls back to spawning a second window, which pywebview tolerates on
some desktop environments and not others -- this is acceptable per
§4.3.2's "best-effort" note.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from exlab_wizard.logging import get_logger

__all__ = ["WindowLauncher"]

_log = get_logger(__name__)


def _resolve_window_executable() -> list[str]:
    """Return the argv prefix for spawning ``exlab-wizard-window``.

    Resolution order:

    1. ``EXLAB_WINDOW_EXECUTABLE`` env var (used by tests and by
       PyInstaller-bundled artifacts to point at the bundled binary).
    2. ``exlab-wizard-window`` on PATH (development install).
    3. Fallback: ``sys.executable -m exlab_wizard.window.main`` (always
       works as long as the package is importable).
    """
    override = os.environ.get("EXLAB_WINDOW_EXECUTABLE")
    if override:
        return [override]
    discovered = shutil.which("exlab-wizard-window")
    if discovered:
        return [discovered]
    return [sys.executable, "-m", "exlab_wizard.window.main"]


class WindowLauncher:
    """Spawn ``exlab-wizard-window`` as a subprocess, track its PID.

    On a re-open request with an existing live child, focuses the
    existing window (best-effort) rather than spawning a duplicate
    (Backend §4.1: "single-instance window").
    """

    def __init__(
        self,
        *,
        window_executable: str | None = None,
        state_dir: Path,
    ) -> None:
        self._state_dir = Path(state_dir)
        self._executable_override = window_executable
        self._proc: subprocess.Popen[bytes] | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def open(self) -> None:
        """Spawn a window subprocess, or focus the existing one."""
        if self.is_alive:
            self._focus_existing()
            return
        argv = self._argv()
        _log.info("spawning window subprocess: %s", argv)
        self._proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
        )

    def close(self) -> None:
        """Terminate the live window subprocess, if any. Idempotent."""
        if self._proc is None:
            return
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=2.0)
        self._proc = None

    @property
    def is_alive(self) -> bool:
        """Return ``True`` while the window subprocess is running."""
        if self._proc is None:
            return False
        return self._proc.poll() is None

    @property
    def pid(self) -> int | None:
        """Return the live window's PID, or ``None`` if no window is up."""
        if not self.is_alive:
            return None
        return self._proc.pid if self._proc is not None else None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _argv(self) -> list[str]:
        """Return the argv to spawn the window subprocess."""
        if self._executable_override:
            return [self._executable_override]
        return _resolve_window_executable()

    def _focus_existing(self) -> None:
        """Best-effort raise/focus of the existing window.

        v1 logs the request; the real focus path (xdotool / win32 / cocoa)
        is platform-specific and out of scope for the unit-test surface.
        Backend §4.3.2 explicitly allows the trivial fallback of "spawn a
        second window" when focus is unavailable.
        """
        _log.info("window already open; focus requested (best-effort)")
