"""Status-submenu rendering. Backend Spec §4.3.2.

The tray's status submenu shows live state derived from server-side
components: ``SessionStore.active_sessions``,
``NASSyncClient.queue_depth``, ``Validator.audit_summary``. The string
follows the §4.3.2 formatter:

* ``"Idle"`` -- nothing in progress.
* ``"Sync: <N> jobs"`` -- N >= 1 NAS-sync jobs in queue.
* ``"<N> plugin needs input"`` -- one or more sessions in
  ``INPUT_REQUIRED`` (the warning emoji is omitted from the canonical
  formatter; pystray menu labels are plain text).
* Combined when multiple conditions hold (e.g. ``"Sync: 2 jobs; 1 plugin
  needs input"``).

A 5-second :class:`StatusTicker` re-evaluates the formatter and notifies
a callback the tray uses to refresh the menu label. The ticker runs on
a daemon thread; tests can drive a single tick deterministically by
calling :meth:`StatusTicker.tick_once`.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from exlab_wizard.logging import get_logger

__all__ = [
    "DEFAULT_REFRESH_SECONDS",
    "StatusSnapshot",
    "StatusTicker",
    "format_status",
    "snapshot_status",
]

_log = get_logger(__name__)

DEFAULT_REFRESH_SECONDS: float = 5.0


@dataclass(frozen=True, slots=True)
class StatusSnapshot:
    """Immutable view over the §4.3.2 status inputs."""

    active_sessions: int = 0
    sync_queue_depth: int = 0
    input_required_count: int = 0


def snapshot_status(
    *,
    session_store: Any = None,
    nas_sync: Any = None,
) -> StatusSnapshot:
    """Build a :class:`StatusSnapshot` from live component references.

    Each component is read defensively so the launcher can pass
    ``None`` (setup-incomplete state) without the formatter crashing.
    The function reads ``active_sessions`` and ``input_required``-style
    counts from ``session_store`` and ``in_flight_jobs`` /
    ``queue_depth`` from ``nas_sync``.
    """
    active = _safe_int(session_store, "active_sessions")
    input_required = _safe_int(session_store, "input_required")
    queue_depth = _safe_int(nas_sync, "queue_depth")
    return StatusSnapshot(
        active_sessions=active,
        sync_queue_depth=queue_depth,
        input_required_count=input_required,
    )


def format_status(snapshot: StatusSnapshot) -> str:
    """Return the menu-label string for the §4.3.2 formatter."""
    parts: list[str] = []
    if snapshot.sync_queue_depth > 0:
        suffix = "job" if snapshot.sync_queue_depth == 1 else "jobs"
        parts.append(f"Sync: {snapshot.sync_queue_depth} {suffix}")
    if snapshot.input_required_count > 0:
        plural = "plugin" if snapshot.input_required_count == 1 else "plugins"
        verb = "needs" if snapshot.input_required_count == 1 else "need"
        parts.append(f"{snapshot.input_required_count} {plural} {verb} input")
    if not parts:
        return "Idle"
    return "; ".join(parts)


class StatusTicker:
    """Polls :func:`snapshot_status` on a fixed cadence.

    On every tick the formatted string is computed; if it differs from
    the previous label the ``on_update`` callback is invoked with the
    new label. The callback is the tray menu's "set status text" hook;
    tests pass a recording callable.
    """

    def __init__(
        self,
        *,
        session_store: Any = None,
        nas_sync: Any = None,
        on_update: Callable[[str], None] | None = None,
        interval_seconds: float = DEFAULT_REFRESH_SECONDS,
    ) -> None:
        self._session_store = session_store
        self._nas_sync = nas_sync
        self._on_update = on_update or (lambda _label: None)
        self._interval = float(interval_seconds)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_label: str | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Spawn the ticker thread. Idempotent."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        thread = threading.Thread(target=self._run, name="exlab-status-ticker", daemon=True)
        thread.start()
        self._thread = thread

    def stop(self) -> None:
        """Signal the ticker to exit. Idempotent."""
        self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None

    def tick_once(self) -> str:
        """Run a single tick synchronously. Returns the new label.

        Tests call this directly so they don't have to wait for a real
        5-second interval.
        """
        snapshot = snapshot_status(session_store=self._session_store, nas_sync=self._nas_sync)
        label = format_status(snapshot)
        if label != self._last_label:
            self._last_label = label
            try:
                self._on_update(label)
            except Exception:
                _log.exception("status-update callback raised")
        return label

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _run(self) -> None:
        while not self._stop.is_set():
            self.tick_once()
            self._stop.wait(self._interval)


def _safe_int(obj: Any, attr: str) -> int:
    """Best-effort numeric read; missing / None / non-int returns 0."""
    if obj is None:
        return 0
    raw = getattr(obj, attr, 0)
    if callable(raw):
        try:
            raw = raw()
        except Exception:
            return 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0
