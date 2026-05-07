"""Tests for :mod:`exlab_wizard.tray.icon`. Backend Spec §4.3.2."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock

import pytest

from exlab_wizard.tray.icon import (
    DEFAULT_ICON_NAME,
    build_icon,
    default_icon_image,
)


class _FakeMenu:
    SEPARATOR = "__SEP__"

    def __init__(self, *items: Any) -> None:
        self.items = items


class _FakeMenuItem:
    def __init__(
        self,
        label: Any,
        action: Callable[..., Any] | None = None,
        *,
        default: bool = False,
        enabled: bool = True,
    ) -> None:
        self.label = label
        self.action = action
        self.default = default
        self.enabled = enabled


class _FakeIcon:
    def __init__(self, name: str, image: Any, title: str, menu: _FakeMenu) -> None:
        self.name = name
        self.image = image
        self.title = title
        self.menu = menu


class _FakePystray:
    Icon = _FakeIcon
    MenuItem = _FakeMenuItem
    Menu = _FakeMenu


def test_build_icon_returns_icon_object() -> None:
    open_calls: list[None] = []
    quit_calls: list[None] = []
    icon = build_icon(
        on_open=lambda: open_calls.append(None),
        on_quit=lambda: quit_calls.append(None),
        status_provider=lambda: "Idle",
        pystray_module=_FakePystray,
        icon_image=object(),
    )
    assert isinstance(icon, _FakeIcon)
    assert icon.name == DEFAULT_ICON_NAME
    assert icon.title == "ExLab-Wizard"
    assert isinstance(icon.menu, _FakeMenu)
    assert len(icon.menu.items) == 4  # Open / Status / SEPARATOR / Quit


def test_open_menu_item_invokes_callback() -> None:
    invoked: list[str] = []
    icon = build_icon(
        on_open=lambda: invoked.append("open"),
        on_quit=lambda: invoked.append("quit"),
        status_provider=lambda: "Idle",
        pystray_module=_FakePystray,
    )
    open_item = icon.menu.items[0]
    assert open_item.label == "Open"
    open_item.action(icon, open_item)
    assert invoked == ["open"]


def test_quit_menu_item_invokes_callback() -> None:
    invoked: list[str] = []
    icon = build_icon(
        on_open=lambda: invoked.append("open"),
        on_quit=lambda: invoked.append("quit"),
        status_provider=lambda: "Idle",
        pystray_module=_FakePystray,
    )
    quit_item = icon.menu.items[3]
    assert quit_item.label == "Quit"
    quit_item.action(icon, quit_item)
    assert invoked == ["quit"]


def test_status_label_callable() -> None:
    icon = build_icon(
        on_open=lambda: None,
        on_quit=lambda: None,
        status_provider=lambda: "Sync: 2 jobs",
        pystray_module=_FakePystray,
    )
    status_item = icon.menu.items[1]
    assert callable(status_item.label)
    assert status_item.enabled is False
    assert status_item.label(status_item) == "Status: Sync: 2 jobs"


def test_callback_exception_is_swallowed() -> None:
    def _raise() -> None:
        raise RuntimeError("boom")

    icon = build_icon(
        on_open=_raise,
        on_quit=_raise,
        status_provider=lambda: "Idle",
        pystray_module=_FakePystray,
    )
    # Should not propagate
    icon.menu.items[0].action(icon, icon.menu.items[0])
    icon.menu.items[3].action(icon, icon.menu.items[3])


def test_default_icon_image_returns_pillow_image() -> None:
    img = default_icon_image()
    # Either a PIL.Image.Image (mode='RGBA') or a fallback BytesIO.
    assert img is not None


def test_lazy_pystray_import(monkeypatch: Any) -> None:
    """When ``pystray_module`` is omitted the lazy import path is taken."""
    fake = MagicMock()
    fake.Menu = _FakePystray.Menu
    fake.MenuItem = _FakePystray.MenuItem
    fake.Icon = _FakePystray.Icon
    monkeypatch.setattr("exlab_wizard.tray.icon._import_pystray", lambda: fake)
    icon = build_icon(
        on_open=lambda: None,
        on_quit=lambda: None,
        status_provider=lambda: "Idle",
    )
    assert isinstance(icon, _FakeIcon)


def test_separator_present() -> None:
    icon = build_icon(
        on_open=lambda: None,
        on_quit=lambda: None,
        status_provider=lambda: "Idle",
        pystray_module=_FakePystray,
    )
    assert _FakePystray.Menu.SEPARATOR in icon.menu.items


def test_import_pystray_returns_module(monkeypatch: pytest.MonkeyPatch) -> None:
    """The lazy-import helper imports pystray when called."""
    import sys

    fake_pystray = type("FakePystray", (), {})
    monkeypatch.setitem(sys.modules, "pystray", fake_pystray)
    from exlab_wizard.tray.icon import _import_pystray

    assert _import_pystray() is fake_pystray
