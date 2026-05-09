"""Per-platform autostart registration. Backend Spec §4.3.2 + §15.7.

Registers ``ExLab-Wizard-Tray`` to launch at user login. Reversible from
``Settings -> Application`` (Frontend §7).

Per-platform mechanism:

* **macOS** -- ``LaunchAgent`` plist at
  ``~/Library/LaunchAgents/com.exlab-wizard.tray.plist`` with
  ``RunAtLoad: true``.
* **Windows** -- ``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run``
  registry entry. Per-user only (no ``HKLM`` writes).
* **Linux** -- ``~/.config/systemd/user/exlab-wizard-tray.service`` plus
  the XDG ``~/.config/autostart/exlab-wizard-tray.desktop`` fallback for
  non-systemd setups (§15.7.1).

Tests inject a ``filesystem_root`` (or set the
``EXLAB_AUTOSTART_ROOT`` env var) so the manager writes to ``tmp_path``
instead of a real per-user location. Real per-OS registration is left
to the launcher in production.
"""

from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path
from typing import Any

from exlab_wizard.constants import Platform
from exlab_wizard.logging import get_logger

__all__ = [
    "AUTOSTART_DESKTOP_NAME",
    "AUTOSTART_PLIST_NAME",
    "AUTOSTART_REG_VALUE",
    "AUTOSTART_SERVICE_NAME",
    "AutostartManager",
]

_log = get_logger(__name__)


# Public constants -- referenced in tests and in §15.7's table.
AUTOSTART_PLIST_NAME = "com.exlab-wizard.tray.plist"
AUTOSTART_SERVICE_NAME = "exlab-wizard-tray.service"
AUTOSTART_DESKTOP_NAME = "exlab-wizard-tray.desktop"
AUTOSTART_REG_VALUE = "ExLabWizard"
_REG_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


class AutostartManager:
    """Per-platform autostart register / unregister / is_registered.

    Construction-time parameters:

    * ``executable_path`` -- absolute path to ``ExLab-Wizard-Tray``. The
      production launcher resolves ``sys.executable`` (PyInstaller
      bundles set ``sys.executable`` to the launcher binary).
    * ``filesystem_root`` -- root directory for per-user files.
      Defaults to ``Path.home()`` in production; tests inject
      ``tmp_path``. The env-var override ``EXLAB_AUTOSTART_ROOT`` wins
      over the constructor default.
    * ``platform`` -- override the OS dispatch, used by tests to
      exercise every branch on a single host.
    """

    def __init__(
        self,
        *,
        executable_path: str | Path | None = None,
        filesystem_root: Path | None = None,
        platform: Platform | str | None = None,
    ) -> None:
        self._executable = str(
            Path(executable_path) if executable_path is not None else Path(sys.executable)
        )
        env_root = os.environ.get("EXLAB_AUTOSTART_ROOT")
        if env_root:
            self._fs_root = Path(env_root)
        elif filesystem_root is not None:
            self._fs_root = Path(filesystem_root)
        else:
            self._fs_root = Path.home()
        self._platform = Platform(platform) if platform is not None else _detect_platform()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_registered(self) -> bool:
        """Return True iff a per-platform autostart record exists."""
        match self._platform:
            case Platform.MACOS:
                return self._plist_path().exists()
            case Platform.WINDOWS:
                return self._win_reg_value() is not None
            case Platform.LINUX:
                return self._systemd_path().exists() or self._desktop_path().exists()

    def register(self) -> None:
        """Create the per-platform autostart record. Idempotent."""
        match self._platform:
            case Platform.MACOS:
                self._write_plist()
            case Platform.WINDOWS:
                self._set_win_reg_value()
            case Platform.LINUX:
                self._register_linux()

    def unregister(self) -> None:
        """Remove the per-platform autostart record. Idempotent."""
        match self._platform:
            case Platform.MACOS:
                _silently_unlink(self._plist_path())
            case Platform.WINDOWS:
                self._delete_win_reg_value()
            case Platform.LINUX:
                _silently_unlink(self._systemd_path())
                _silently_unlink(self._desktop_path())

    @property
    def executable_path(self) -> str:
        """Return the absolute path the autostart record points at."""
        return self._executable

    @property
    def platform(self) -> Platform:
        """Return the platform dispatch this manager is configured for."""
        return self._platform

    # ------------------------------------------------------------------
    # macOS LaunchAgent
    # ------------------------------------------------------------------

    def _plist_path(self) -> Path:
        return self._fs_root / "Library" / "LaunchAgents" / AUTOSTART_PLIST_NAME

    def _write_plist(self) -> None:
        path = self._plist_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_render_plist(self._executable), encoding="utf-8")
        _log.info("registered macOS LaunchAgent at %s", path)

    # ------------------------------------------------------------------
    # Linux: systemd + .desktop fallback
    # ------------------------------------------------------------------

    def _systemd_path(self) -> Path:
        return self._fs_root / ".config" / "systemd" / "user" / AUTOSTART_SERVICE_NAME

    def _desktop_path(self) -> Path:
        return self._fs_root / ".config" / "autostart" / AUTOSTART_DESKTOP_NAME

    def _register_linux(self) -> None:
        # Spec §15.7.1: prefer systemd-user; fall back to XDG .desktop.
        self._write_systemd_unit()
        self._write_desktop_file()
        _log.info("registered Linux autostart (systemd + .desktop fallback)")

    def _write_systemd_unit(self) -> None:
        path = self._systemd_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_render_systemd_unit(self._executable), encoding="utf-8")

    def _write_desktop_file(self) -> None:
        path = self._desktop_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_render_desktop_file(self._executable), encoding="utf-8")

    # ------------------------------------------------------------------
    # Windows registry
    # ------------------------------------------------------------------

    def _set_win_reg_value(self) -> None:
        winreg = _import_winreg()
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, AUTOSTART_REG_VALUE, 0, winreg.REG_SZ, self._executable)
        _log.info("registered Windows Run value %s", AUTOSTART_REG_VALUE)

    def _delete_win_reg_value(self) -> None:
        winreg = _import_winreg()
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, _REG_RUN_KEY, 0, winreg.KEY_SET_VALUE
            ) as key:
                winreg.DeleteValue(key, AUTOSTART_REG_VALUE)
        except FileNotFoundError:
            pass

    def _win_reg_value(self) -> str | None:
        winreg = _import_winreg()
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, _REG_RUN_KEY, 0, winreg.KEY_QUERY_VALUE
            ) as key:
                value, _kind = winreg.QueryValueEx(key, AUTOSTART_REG_VALUE)
                return str(value)
        except FileNotFoundError:
            return None


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def _render_plist(executable: str) -> str:
    """Return the macOS LaunchAgent plist body. Backend Spec §15.7.1."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        "<dict>\n"
        "    <key>Label</key>\n"
        "    <string>com.exlab-wizard.tray</string>\n"
        "    <key>ProgramArguments</key>\n"
        "    <array>\n"
        f"        <string>{executable}</string>\n"
        "    </array>\n"
        "    <key>RunAtLoad</key>\n"
        "    <true/>\n"
        "    <key>KeepAlive</key>\n"
        "    <dict>\n"
        "        <key>SuccessfulExit</key>\n"
        "        <false/>\n"
        "    </dict>\n"
        "</dict>\n"
        "</plist>\n"
    )


def _render_systemd_unit(executable: str) -> str:
    """Return the Linux systemd-user unit body. Backend Spec §15.7.1."""
    return (
        "[Unit]\n"
        "Description=ExLab-Wizard tray\n"
        "\n"
        "[Service]\n"
        f"ExecStart={executable}\n"
        "Restart=on-failure\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def _render_desktop_file(executable: str) -> str:
    """Return the XDG ``.desktop`` autostart body. Backend Spec §15.7.1."""
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=ExLab-Wizard\n"
        f"Exec={executable}\n"
        "X-GNOME-Autostart-enabled=true\n"
        "NoDisplay=false\n"
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _detect_platform() -> Platform:
    if sys.platform == "darwin":
        return Platform.MACOS
    if sys.platform == "win32":
        return Platform.WINDOWS
    return Platform.LINUX


def _silently_unlink(path: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        path.unlink()


def _import_winreg() -> Any:
    """Import ``winreg`` lazily so the module loads on macOS / Linux too.

    Returns the imported module (typed as ``Any`` since the stdlib's
    ``winreg`` constants and functions are only typed on Windows; using
    ``Any`` here keeps the registry-specific call sites readable without
    per-attribute ``# type: ignore`` markers). Raises ``RuntimeError``
    when called on a non-Windows host without a winreg shim mounted
    (tests inject a fake under ``sys.modules['winreg']`` to exercise the
    registry path).
    """
    try:
        import winreg  # type: ignore[import-not-found]

        return winreg
    except ImportError as exc:  # pragma: no cover -- only on non-Windows
        msg = "winreg unavailable on this platform"
        raise RuntimeError(msg) from exc
