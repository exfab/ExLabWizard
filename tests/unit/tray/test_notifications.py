"""Tests for :mod:`exlab_wizard.tray.notifications`. Backend Spec §15.7.3."""

from __future__ import annotations

import time
from typing import Any

import pytest

from exlab_wizard.tray.notifications import (
    APP_NAME,
    COALESCING_WINDOW_SECONDS,
    NotificationBus,
    _coalesced_message,
    notify,
)


def test_notify_calls_injected_callable() -> None:
    seen: list[dict[str, Any]] = []

    def _record(**kwargs: Any) -> None:
        seen.append(kwargs)

    notify(title="X", message="Y", notifier=_record)
    assert seen == [{"title": "X", "message": "Y", "app_name": APP_NAME, "timeout": 10}]


def test_notify_swallows_notifier_exceptions() -> None:
    def _boom(**_kwargs: Any) -> None:
        raise RuntimeError("nope")

    # Must not raise.
    notify(title="X", message="Y", notifier=_boom)


def test_bus_emits_after_coalescing_window() -> None:
    seen: list[dict[str, Any]] = []

    def _record(**kwargs: Any) -> None:
        seen.append(kwargs)

    bus = NotificationBus(
        notifier=_record,
        is_window_foregrounded=lambda: False,
        coalescing_window=10.0,  # never auto-fire during the test
    )
    bus.emit(
        kind="plugin_input_required",
        title="ExLab-Wizard",
        message="1 plugin needs input",
    )
    bus.emit(
        kind="plugin_input_required",
        title="ExLab-Wizard",
        message="2 plugins need input",
    )
    bus.flush_pending()
    assert len(seen) == 1
    assert "2 plugins need input" in seen[0]["message"]


def test_bus_suppresses_when_window_foregrounded() -> None:
    seen: list[dict[str, Any]] = []
    bus = NotificationBus(
        notifier=lambda **kw: seen.append(kw),
        is_window_foregrounded=lambda: True,
    )
    bus.emit(kind="sync_failed", title="X", message="Y")
    bus.flush_pending()
    assert seen == []


def test_bus_separate_kinds_separate_buckets() -> None:
    seen: list[dict[str, Any]] = []
    bus = NotificationBus(
        notifier=lambda **kw: seen.append(kw),
        is_window_foregrounded=lambda: False,
        coalescing_window=10.0,
    )
    bus.emit(kind="sync_failed", title="A", message="m1")
    bus.emit(kind="plugin_input_required", title="B", message="m2")
    bus.flush_pending()
    assert len(seen) == 2
    messages = {item["message"] for item in seen}
    assert messages == {"m1", "m2"}


def test_bus_cancel_all_drops_pending() -> None:
    seen: list[dict[str, Any]] = []
    bus = NotificationBus(
        notifier=lambda **kw: seen.append(kw),
        is_window_foregrounded=lambda: False,
        coalescing_window=10.0,
    )
    bus.emit(kind="sync_failed", title="A", message="x")
    bus.cancel_all()
    bus.flush_pending()
    assert seen == []


def test_bus_emits_singleton_message_unchanged() -> None:
    seen: list[dict[str, Any]] = []
    bus = NotificationBus(
        notifier=lambda **kw: seen.append(kw),
        is_window_foregrounded=lambda: False,
        coalescing_window=10.0,
    )
    bus.emit(kind="sync_failed", title="X", message="hello")
    bus.flush_pending()
    assert seen[0]["message"] == "hello"


def test_coalescing_window_constant_matches_spec() -> None:
    assert COALESCING_WINDOW_SECONDS == 5.0


def test_coalesced_message_plugin_input_required_plural() -> None:
    assert _coalesced_message("plugin_input_required", 2) == "2 plugins need input"


def test_coalesced_message_plugin_input_required_singular() -> None:
    assert _coalesced_message("plugin_input_required", 1) == "1 plugin needs input"


def test_coalesced_message_sync_failed_plural() -> None:
    assert _coalesced_message("sync_failed", 3) == "3 sync failures"


def test_coalesced_message_sync_failed_singular() -> None:
    assert _coalesced_message("sync_failed", 1) == "1 sync failure"


def test_coalesced_message_unknown_kind() -> None:
    assert _coalesced_message("custom", 5) == "5 custom events"


def test_bus_timer_fires_after_window() -> None:
    """Drive the real timer with a tiny window."""
    seen: list[dict[str, Any]] = []
    bus = NotificationBus(
        notifier=lambda **kw: seen.append(kw),
        is_window_foregrounded=lambda: False,
        coalescing_window=0.05,
    )
    bus.emit(kind="sync_failed", title="X", message="m")
    deadline = time.monotonic() + 1.0
    while not seen and time.monotonic() < deadline:
        time.sleep(0.01)
    assert seen, "timer should have fired the notification"


def test_default_notifier_returns_callable() -> None:
    """The default notifier resolves either to plyer or to the noop fallback."""
    from exlab_wizard.tray.notifications import _default_notifier

    fn = _default_notifier()
    assert callable(fn)


def test_noop_notifier_swallows_arbitrary_kwargs() -> None:
    """Fallback notifier is keyword-tolerant."""
    from exlab_wizard.tray.notifications import _noop_notifier

    _noop_notifier(title="x", message="y", app_name="z", timeout=1)


def test_notify_uses_default_notifier_when_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "exlab_wizard.tray.notifications._default_notifier",
        lambda: lambda **kw: seen.append(kw),
    )
    notify(title="X", message="Y")
    assert seen and seen[0]["title"] == "X"


def test_flush_with_already_drained_bucket() -> None:
    """``_flush`` is safe when the bucket has already been popped (race)."""
    seen: list[dict[str, Any]] = []
    bus = NotificationBus(
        notifier=lambda **kw: seen.append(kw),
        is_window_foregrounded=lambda: False,
        coalescing_window=10.0,
    )
    bus.emit(kind="sync_failed", title="X", message="m")
    # Simulate cancel_all happening before timer fires.
    bus.cancel_all()
    bus._flush("sync_failed")  # Should be a no-op.
    assert seen == []
