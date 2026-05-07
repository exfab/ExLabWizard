"""Tests for :mod:`exlab_wizard.tray.autostart`. Backend Spec §15.7."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from exlab_wizard.tray.autostart import (
    AUTOSTART_DESKTOP_NAME,
    AUTOSTART_PLIST_NAME,
    AUTOSTART_REG_VALUE,
    AUTOSTART_SERVICE_NAME,
    AutostartManager,
    _detect_platform,
)

# ---------------------------------------------------------------------------
# macOS LaunchAgent
# ---------------------------------------------------------------------------


def test_macos_register_writes_plist(tmp_path: Path) -> None:
    mgr = AutostartManager(
        executable_path="/Applications/X.app/Contents/MacOS/X",
        filesystem_root=tmp_path,
        platform="macos",
    )
    assert mgr.is_registered() is False
    mgr.register()
    plist = tmp_path / "Library" / "LaunchAgents" / AUTOSTART_PLIST_NAME
    assert plist.exists()
    body = plist.read_text()
    assert "/Applications/X.app/Contents/MacOS/X" in body
    assert "<key>RunAtLoad</key>" in body
    assert mgr.is_registered() is True


def test_macos_unregister_removes_plist(tmp_path: Path) -> None:
    mgr = AutostartManager(
        executable_path="/usr/local/bin/exlab",
        filesystem_root=tmp_path,
        platform="macos",
    )
    mgr.register()
    mgr.unregister()
    assert mgr.is_registered() is False


def test_macos_register_is_idempotent(tmp_path: Path) -> None:
    mgr = AutostartManager(executable_path="/x", filesystem_root=tmp_path, platform="macos")
    mgr.register()
    mgr.register()
    assert mgr.is_registered() is True


# ---------------------------------------------------------------------------
# Linux: systemd + .desktop fallback
# ---------------------------------------------------------------------------


def test_linux_register_writes_systemd_and_desktop(tmp_path: Path) -> None:
    mgr = AutostartManager(
        executable_path="/opt/exlab/bin/tray",
        filesystem_root=tmp_path,
        platform="linux",
    )
    assert mgr.is_registered() is False
    mgr.register()
    systemd = tmp_path / ".config" / "systemd" / "user" / AUTOSTART_SERVICE_NAME
    desktop = tmp_path / ".config" / "autostart" / AUTOSTART_DESKTOP_NAME
    assert systemd.exists()
    assert desktop.exists()
    assert "/opt/exlab/bin/tray" in systemd.read_text()
    assert "/opt/exlab/bin/tray" in desktop.read_text()
    assert mgr.is_registered() is True


def test_linux_unregister_removes_both_files(tmp_path: Path) -> None:
    mgr = AutostartManager(executable_path="/opt/x", filesystem_root=tmp_path, platform="linux")
    mgr.register()
    mgr.unregister()
    assert mgr.is_registered() is False


def test_linux_is_registered_when_only_desktop_present(tmp_path: Path) -> None:
    mgr = AutostartManager(executable_path="/opt/x", filesystem_root=tmp_path, platform="linux")
    desktop = tmp_path / ".config" / "autostart" / AUTOSTART_DESKTOP_NAME
    desktop.parent.mkdir(parents=True, exist_ok=True)
    desktop.write_text("[Desktop Entry]\nExec=/opt/x\n")
    assert mgr.is_registered() is True


# ---------------------------------------------------------------------------
# Windows registry (faked via sys.modules)
# ---------------------------------------------------------------------------


class _FakeKey:
    def __init__(self, store: dict[str, str], permission: int) -> None:
        self.store = store
        self.permission = permission

    def __enter__(self) -> _FakeKey:
        return self

    def __exit__(self, *args: object) -> None:
        return None


class _FakeWinreg:
    HKEY_CURRENT_USER = "HKCU"
    KEY_SET_VALUE = 1
    KEY_QUERY_VALUE = 2
    REG_SZ = 1

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.opens: list[tuple[str, str, int]] = []

    def OpenKey(
        self,
        hkey: str,
        subkey: str,
        reserved: int,
        permission: int,
    ) -> _FakeKey:
        _ = reserved
        self.opens.append((hkey, subkey, permission))
        return _FakeKey(self.values, permission)

    def SetValueEx(
        self,
        key: _FakeKey,
        name: str,
        reserved: int,
        kind: int,
        value: str,
    ) -> None:
        _ = reserved
        _ = kind
        key.store[name] = value

    def QueryValueEx(self, key: _FakeKey, name: str) -> tuple[str, int]:
        if name not in key.store:
            raise FileNotFoundError(name)
        return key.store[name], self.REG_SZ

    def DeleteValue(self, key: _FakeKey, name: str) -> None:
        key.store.pop(name, None)


@pytest.fixture
def fake_winreg(monkeypatch: pytest.MonkeyPatch) -> _FakeWinreg:
    fake = _FakeWinreg()
    monkeypatch.setitem(sys.modules, "winreg", fake)
    return fake


def test_windows_register_sets_run_value(fake_winreg: _FakeWinreg, tmp_path: Path) -> None:
    mgr = AutostartManager(
        executable_path=r"C:\Program Files\ExLab\Tray.exe",
        filesystem_root=tmp_path,
        platform="windows",
    )
    assert mgr.is_registered() is False
    mgr.register()
    assert fake_winreg.values[AUTOSTART_REG_VALUE] == r"C:\Program Files\ExLab\Tray.exe"
    assert mgr.is_registered() is True


def test_windows_unregister_deletes_run_value(fake_winreg: _FakeWinreg, tmp_path: Path) -> None:
    mgr = AutostartManager(
        executable_path=r"C:\X\Y.exe",
        filesystem_root=tmp_path,
        platform="windows",
    )
    mgr.register()
    mgr.unregister()
    assert AUTOSTART_REG_VALUE not in fake_winreg.values
    assert mgr.is_registered() is False


def test_windows_unregister_when_value_missing_is_safe(
    fake_winreg: _FakeWinreg, tmp_path: Path
) -> None:
    _ = fake_winreg
    mgr = AutostartManager(executable_path="X", filesystem_root=tmp_path, platform="windows")
    mgr.unregister()  # idempotent: no value to begin with.


def test_windows_open_key_handles_filenotfound_on_query(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When ``OpenKey`` raises FileNotFoundError, ``is_registered`` returns False."""

    class _ErrorWinreg(_FakeWinreg):
        def OpenKey(self, *args: object, **kwargs: object) -> _FakeKey:
            _ = args
            _ = kwargs
            raise FileNotFoundError

    monkeypatch.setitem(sys.modules, "winreg", _ErrorWinreg())
    mgr = AutostartManager(executable_path="X", filesystem_root=tmp_path, platform="windows")
    assert mgr.is_registered() is False
    # unregister should also be tolerant of the same error.
    mgr.unregister()


def test_silently_unlink_tolerates_missing(tmp_path: Path) -> None:
    """Internal helper tolerates a missing path without raising."""
    from exlab_wizard.tray.autostart import _silently_unlink

    _silently_unlink(tmp_path / "does-not-exist")


# ---------------------------------------------------------------------------
# Cross-cutting
# ---------------------------------------------------------------------------


def test_env_var_root_overrides_constructor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv("EXLAB_AUTOSTART_ROOT", str(other))
    mgr = AutostartManager(
        executable_path="X",
        filesystem_root=tmp_path / "ignored",
        platform="macos",
    )
    mgr.register()
    assert (other / "Library" / "LaunchAgents" / AUTOSTART_PLIST_NAME).exists()


def test_executable_path_property(tmp_path: Path) -> None:
    mgr = AutostartManager(executable_path="/x", filesystem_root=tmp_path, platform="linux")
    assert mgr.executable_path == "/x"
    assert mgr.platform == "linux"


def test_default_executable_falls_back_to_sys_executable(tmp_path: Path) -> None:
    mgr = AutostartManager(filesystem_root=tmp_path, platform="linux")
    # Whatever sys.executable is, the manager records it.
    assert mgr.executable_path == sys.executable


def test_detect_platform_returns_known_value() -> None:
    plat = _detect_platform()
    assert plat in {"macos", "windows", "linux"}


def test_detect_platform_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.platform", "darwin")
    assert _detect_platform() == "macos"
    monkeypatch.setattr("sys.platform", "win32")
    assert _detect_platform() == "windows"
    monkeypatch.setattr("sys.platform", "linux")
    assert _detect_platform() == "linux"
