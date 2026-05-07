"""Tests for :mod:`exlab_wizard.window.pywebview_app`. Backend Spec §15.3.2."""

from __future__ import annotations

from typing import Any

import pytest

from exlab_wizard.window.main import ServerHandshake
from exlab_wizard.window.pywebview_app import (
    DEFAULT_HEIGHT,
    DEFAULT_WIDTH,
    WINDOW_TITLE,
    build_window_url,
    is_debug_enabled,
    run_window,
)


def _hs(port: int = 8000) -> ServerHandshake:
    return ServerHandshake(port=port, pid=1, started_at="2026")


def test_build_window_url_uses_loopback() -> None:
    assert build_window_url(_hs(port=8123)) == "http://127.0.0.1:8123"


def test_is_debug_enabled_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EXLAB_DEBUG", raising=False)
    assert is_debug_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "ON"])
def test_is_debug_enabled_truthy(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("EXLAB_DEBUG", value)
    assert is_debug_enabled() is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off"])
def test_is_debug_enabled_falsy(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("EXLAB_DEBUG", value)
    assert is_debug_enabled() is False


def test_run_window_calls_create_and_start() -> None:
    create_calls: list[dict[str, Any]] = []
    start_calls: list[dict[str, Any]] = []

    def _create(**kwargs: Any) -> None:
        create_calls.append(kwargs)

    def _start(**kwargs: Any) -> None:
        start_calls.append(kwargs)

    rc = run_window(_hs(port=4321), create_window=_create, start=_start)
    assert rc == 0
    assert create_calls[0]["url"] == "http://127.0.0.1:4321"
    assert create_calls[0]["title"] == WINDOW_TITLE
    assert create_calls[0]["width"] == DEFAULT_WIDTH
    assert create_calls[0]["height"] == DEFAULT_HEIGHT
    assert start_calls[0]["debug"] is False


def test_run_window_passes_debug_when_env_var_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EXLAB_DEBUG", "1")
    seen: list[bool] = []

    def _start(**kwargs: Any) -> None:
        seen.append(kwargs["debug"])

    run_window(_hs(), create_window=lambda **_: None, start=_start)
    assert seen == [True]


def test_run_window_with_only_create_window_uses_default_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``start`` is omitted the lazy import path is taken."""
    sentinel = []
    fake_webview = type(
        "W",
        (),
        {
            "create_window": lambda **kwargs: sentinel.append(("create", kwargs)),
            "start": lambda **kwargs: sentinel.append(("start", kwargs)),
        },
    )
    monkeypatch.setattr("exlab_wizard.window.pywebview_app._import_webview", lambda: fake_webview)
    rc = run_window(_hs())
    assert rc == 0
    assert any(call[0] == "create" for call in sentinel)
    assert any(call[0] == "start" for call in sentinel)


def test_window_title_constant() -> None:
    assert WINDOW_TITLE == "ExLab-Wizard"


def test_import_webview_returns_module(monkeypatch: pytest.MonkeyPatch) -> None:
    """The lazy-import helper imports ``webview`` when called."""
    import sys

    fake_webview = type("FakeWebview", (), {})
    monkeypatch.setitem(sys.modules, "webview", fake_webview)
    from exlab_wizard.window.pywebview_app import _import_webview

    assert _import_webview() is fake_webview
