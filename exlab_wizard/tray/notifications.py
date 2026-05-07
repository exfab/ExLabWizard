"""OS notifications via plyer with 5-second coalescing. Backend Spec §15.7.3.

Two notification triggers (Backend §15.7.3 / Frontend §3.4.5):

* ``PluginInputRequired`` escalation when the window is not in the
  foreground.
* NAS-sync failure with no auto-retry budget left.

Coalescing rule: a burst of ``N`` triggers within the same 5-second
window collapses to one notification with a count
(*"ExLab-Wizard: N plugins need input"*). Notifications are suppressed
entirely when ``window_foregrounded`` is ``True``; the operator is
already looking at the UI in that case.

This module is the long-lived process's OS-notification surface.
``ui/notifications.py`` is a separate module covering NiceGUI in-window
notifications -- distinct concern, distinct file.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from exlab_wizard.logging import get_logger

__all__ = [
    "COALESCING_WINDOW_SECONDS",
    "NotificationBus",
    "TriggerKind",
    "notify",
]

_log = get_logger(__name__)

COALESCING_WINDOW_SECONDS: float = 5.0
APP_NAME = "ExLab-Wizard"


# Trigger labels used as the coalescing key. Two enum-like strings is
# enough; we deliberately keep this an internal vocabulary rather than a
# stdlib Enum because the bus is also called from the API layer when
# integration code grows.
TriggerKind = str  # "plugin_input_required" | "sync_failed" | ad-hoc strings


def notify(
    *,
    title: str,
    message: str,
    notifier: Callable[..., Any] | None = None,
) -> None:
    """Fire a plyer notification (or an injected stub).

    ``notifier`` defaults to ``plyer.notification.notify`` which the
    plyer wheel auto-resolves to the platform-appropriate backend
    (UNUserNotificationCenter / ToastNotificationManager / libnotify).
    Tests pass a recording callable.
    """
    fn = notifier if notifier is not None else _default_notifier()
    try:
        fn(title=title, message=message, app_name=APP_NAME, timeout=10)
    except Exception:
        _log.exception("OS notification failed (title=%r)", title)


def _default_notifier() -> Callable[..., Any]:
    """Resolve the plyer notifier lazily; safe even when plyer's backend errors."""
    try:
        from plyer import notification

        return notification.notify
    except Exception:  # pragma: no cover -- plyer import path
        return _noop_notifier


def _noop_notifier(**_kwargs: Any) -> None:
    _log.warning("plyer notification backend unavailable; suppressing")


# ---------------------------------------------------------------------------
# Coalescing bus
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _Bucket:
    """Per-trigger coalescing bucket."""

    count: int = 0
    earliest: float = field(default_factory=time.monotonic)
    timer: threading.Timer | None = None
    last_message: str = ""


class NotificationBus:
    """Coalescing layer over :func:`notify`.

    The launcher constructs one bus and threads it through to the
    components that emit notification-eligible events (plugin host,
    NAS-sync). Components call :meth:`emit`; the bus handles
    coalescing, foreground suppression, and the actual plyer call.
    """

    def __init__(
        self,
        *,
        notifier: Callable[..., Any] | None = None,
        is_window_foregrounded: Callable[[], bool] | None = None,
        coalescing_window: float = COALESCING_WINDOW_SECONDS,
    ) -> None:
        self._notifier = notifier
        self._is_foreground = is_window_foregrounded or (lambda: False)
        self._window = float(coalescing_window)
        self._buckets: dict[TriggerKind, _Bucket] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def emit(self, *, kind: TriggerKind, title: str, message: str) -> None:
        """Submit a notification trigger.

        If the window is foregrounded the call is dropped silently.
        Otherwise the trigger is added to its coalescing bucket; the
        first trigger in a window arms a timer that fires the actual
        notification at window end.
        """
        _ = title  # title is fixed per-bucket, derived inside ``_flush``
        if self._is_foreground():
            _log.debug("notification suppressed (window foregrounded): %s", kind)
            return

        with self._lock:
            bucket = self._buckets.get(kind)
            if bucket is None:
                bucket = _Bucket()
                self._buckets[kind] = bucket
                bucket.last_message = message
                bucket.count = 1
                bucket.earliest = time.monotonic()
                bucket.timer = threading.Timer(self._window, self._flush, args=(kind,))
                bucket.timer.daemon = True
                bucket.timer.start()
            else:
                bucket.count += 1
                bucket.last_message = message

    def flush_pending(self) -> int:
        """Drain every active bucket synchronously. Returns the bucket count.

        Used by tests and by the quit coordinator to make sure no
        in-flight coalesce-buckets are dropped on shutdown.
        """
        kinds: list[str]
        with self._lock:
            kinds = list(self._buckets.keys())
        for kind in kinds:
            self._flush(kind)
        return len(kinds)

    def cancel_all(self) -> None:
        """Cancel every active timer without firing the notifications."""
        with self._lock:
            for bucket in self._buckets.values():
                if bucket.timer is not None:
                    bucket.timer.cancel()
            self._buckets.clear()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _flush(self, kind: TriggerKind) -> None:
        with self._lock:
            bucket = self._buckets.pop(kind, None)
            if bucket is None:
                return
            if bucket.timer is not None:
                bucket.timer.cancel()
        title = APP_NAME
        message = bucket.last_message
        if bucket.count > 1:
            message = _coalesced_message(kind, bucket.count)
        notify(title=title, message=message, notifier=self._notifier)


def _coalesced_message(kind: TriggerKind, count: int) -> str:
    """Render the coalesced summary string for a burst of ``count`` events."""
    if kind == "plugin_input_required":
        plural = "plugin" if count == 1 else "plugins"
        verb = "needs" if count == 1 else "need"
        return f"{count} {plural} {verb} input"
    if kind == "sync_failed":
        plural = "failure" if count == 1 else "failures"
        return f"{count} sync {plural}"
    return f"{count} {kind} events"
