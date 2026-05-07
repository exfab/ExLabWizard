"""Tests for :mod:`exlab_wizard.tray.status`. Backend Spec §4.3.2."""

from __future__ import annotations

import threading
from typing import Any

from exlab_wizard.tray.status import (
    DEFAULT_REFRESH_SECONDS,
    StatusSnapshot,
    StatusTicker,
    format_status,
    snapshot_status,
)


class _StubStore:
    def __init__(self, *, active_sessions: int = 0, input_required: int = 0) -> None:
        self.active_sessions = active_sessions
        self.input_required = input_required


class _StubSync:
    def __init__(self, *, queue_depth: int = 0) -> None:
        self.queue_depth = queue_depth


def test_format_status_idle() -> None:
    snap = StatusSnapshot()
    assert format_status(snap) == "Idle"


def test_format_status_sync_jobs() -> None:
    snap = StatusSnapshot(sync_queue_depth=3)
    assert format_status(snap) == "Sync: 3 jobs"


def test_format_status_single_job_uses_singular() -> None:
    snap = StatusSnapshot(sync_queue_depth=1)
    assert format_status(snap) == "Sync: 1 job"


def test_format_status_input_required_singular() -> None:
    snap = StatusSnapshot(input_required_count=1)
    assert format_status(snap) == "1 plugin needs input"


def test_format_status_input_required_plural() -> None:
    snap = StatusSnapshot(input_required_count=4)
    assert format_status(snap) == "4 plugins need input"


def test_format_status_combines_sync_and_input_required() -> None:
    snap = StatusSnapshot(sync_queue_depth=2, input_required_count=1)
    assert format_status(snap) == "Sync: 2 jobs; 1 plugin needs input"


def test_snapshot_status_with_none_components() -> None:
    snap = snapshot_status()
    assert snap.active_sessions == 0
    assert snap.sync_queue_depth == 0
    assert snap.input_required_count == 0


def test_snapshot_status_reads_components() -> None:
    snap = snapshot_status(
        session_store=_StubStore(active_sessions=2, input_required=1),
        nas_sync=_StubSync(queue_depth=5),
    )
    assert snap.active_sessions == 2
    assert snap.input_required_count == 1
    assert snap.sync_queue_depth == 5


def test_snapshot_status_callable_attributes() -> None:
    class _CallableStore:
        active_sessions = staticmethod(lambda: 7)
        input_required = staticmethod(lambda: 0)

    snap = snapshot_status(session_store=_CallableStore())
    assert snap.active_sessions == 7


def test_ticker_invokes_callback_on_first_tick() -> None:
    seen: list[str] = []
    ticker = StatusTicker(
        session_store=_StubStore(active_sessions=0, input_required=0),
        nas_sync=_StubSync(queue_depth=2),
        on_update=seen.append,
        interval_seconds=0.05,
    )
    label = ticker.tick_once()
    assert label == "Sync: 2 jobs"
    assert seen == ["Sync: 2 jobs"]


def test_ticker_does_not_invoke_callback_when_label_unchanged() -> None:
    seen: list[str] = []
    ticker = StatusTicker(
        session_store=_StubStore(),
        nas_sync=_StubSync(),
        on_update=seen.append,
    )
    ticker.tick_once()
    ticker.tick_once()
    assert seen == ["Idle"]  # only once


def test_ticker_start_and_stop_idempotent() -> None:
    ticker = StatusTicker(interval_seconds=0.05)
    ticker.start()
    ticker.start()  # second start is no-op
    ticker.stop()
    ticker.stop()  # second stop is no-op


def test_ticker_callback_exception_is_swallowed() -> None:
    def _boom(_label: str) -> None:
        raise RuntimeError("boom")

    ticker = StatusTicker(on_update=_boom)
    # Should NOT raise.
    label = ticker.tick_once()
    assert label == "Idle"


def test_ticker_thread_runs(monkeypatch: Any) -> None:
    """Drive the ticker thread for a short window and confirm callback fires."""
    seen: list[str] = []
    event = threading.Event()

    def _record(label: str) -> None:
        seen.append(label)
        event.set()

    ticker = StatusTicker(
        session_store=_StubStore(input_required=1),
        on_update=_record,
        interval_seconds=0.01,
    )
    ticker.start()
    try:
        assert event.wait(0.5)
    finally:
        ticker.stop()
    assert seen[0] == "1 plugin needs input"


def test_default_refresh_matches_spec() -> None:
    # Backend §4.3.2: 5-second refresh ticker.
    assert DEFAULT_REFRESH_SECONDS == 5.0


def test_safe_int_handles_callable_that_raises() -> None:
    from exlab_wizard.tray.status import _safe_int

    class _Holder:
        def value(self) -> int:
            raise RuntimeError("boom")

    assert _safe_int(_Holder(), "value") == 0


def test_safe_int_handles_non_numeric() -> None:
    from exlab_wizard.tray.status import _safe_int

    class _Holder:
        attr = "not-a-number"

    assert _safe_int(_Holder(), "attr") == 0
