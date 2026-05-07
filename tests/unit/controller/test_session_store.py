"""Tests for ``exlab_wizard.controller.session_store``.

The session store is the in-memory bookkeeping for in-flight sessions
(Backend Spec §4.4.7). These tests pin:

- ``open`` returns a fresh PENDING session with a UUID4 id.
- ``transition`` updates state + ``current_phase`` together.
- ``get`` returns ``None`` for unknown ids.
- ``attach_event_queue`` wires up the WebSocket fan-out queue.
- ``heartbeat`` refreshes ``last_heartbeat``.
- ``abandoned_older_than`` filters to ``INPUT_REQUIRED`` only.
- ``gc_loop`` closes abandoned ``INPUT_REQUIRED`` sessions to ``ABORTED``.
- ``close`` stamps the terminal-state outcome.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from exlab_wizard.controller.session_store import Session, SessionStore
from exlab_wizard.controller.state_machine import Phase, SessionState

# ---------------------------------------------------------------------------
# open
# ---------------------------------------------------------------------------


def test_open_returns_fresh_pending_session() -> None:
    store = SessionStore()
    session = store.open("project", req={"label": "x"})
    assert session.state is SessionState.PENDING
    assert session.kind == "project"
    assert session.request == {"label": "x"}
    assert session.current_phase is None
    assert session.next_action == "none"


def test_open_assigns_uuid4_session_id() -> None:
    store = SessionStore()
    session = store.open("run", req={})
    # Round-trip through uuid.UUID to validate format.
    parsed = uuid.UUID(session.session_id)
    assert parsed.version == 4


def test_open_two_sessions_get_distinct_ids() -> None:
    store = SessionStore()
    a = store.open("project", {})
    b = store.open("project", {})
    assert a.session_id != b.session_id


def test_open_sets_created_at_and_last_heartbeat_to_now() -> None:
    store = SessionStore()
    before = datetime.now(tz=UTC)
    session = store.open("run", {})
    after = datetime.now(tz=UTC)
    assert before <= session.created_at <= after
    assert before <= session.last_heartbeat <= after


# ---------------------------------------------------------------------------
# get
# ---------------------------------------------------------------------------


def test_get_returns_none_for_unknown_session_id() -> None:
    store = SessionStore()
    assert store.get("does-not-exist") is None


def test_get_returns_session_after_open() -> None:
    store = SessionStore()
    session = store.open("project", {})
    fetched = store.get(session.session_id)
    assert fetched is session


# ---------------------------------------------------------------------------
# transition
# ---------------------------------------------------------------------------


def test_transition_updates_state_and_phase_together() -> None:
    store = SessionStore()
    session = store.open("project", {})
    store.transition(session.session_id, SessionState.VALIDATING)
    assert session.state is SessionState.VALIDATING
    assert session.current_phase is Phase.VALIDATING_INPUTS


def test_transition_to_input_required_sets_next_action_awaiting_input() -> None:
    store = SessionStore()
    session = store.open("project", {})
    store.transition(session.session_id, SessionState.VALIDATING)
    store.transition(session.session_id, SessionState.RENDERING)
    store.transition(session.session_id, SessionState.PLUGIN_PASS)
    store.transition(session.session_id, SessionState.INPUT_REQUIRED)
    assert session.next_action == "awaiting_input"


def test_transition_back_to_plugin_pass_clears_next_action() -> None:
    store = SessionStore()
    session = store.open("project", {})
    for state in (
        SessionState.VALIDATING,
        SessionState.RENDERING,
        SessionState.PLUGIN_PASS,
        SessionState.INPUT_REQUIRED,
    ):
        store.transition(session.session_id, state)
    assert session.next_action == "awaiting_input"
    store.transition(session.session_id, SessionState.PLUGIN_PASS)
    assert session.next_action == "none"


def test_transition_rejects_unknown_session_id() -> None:
    store = SessionStore()
    with pytest.raises(ValueError, match="unknown session_id"):
        store.transition("nope", SessionState.VALIDATING)


def test_transition_rejects_illegal_state_transition() -> None:
    store = SessionStore()
    session = store.open("project", {})
    with pytest.raises(ValueError, match="illegal state transition"):
        store.transition(session.session_id, SessionState.RENDERING)  # skips VALIDATING


# ---------------------------------------------------------------------------
# attach_event_queue
# ---------------------------------------------------------------------------


async def test_attach_event_queue_wires_queue_to_session() -> None:
    store = SessionStore()
    session = store.open("project", {})
    queue: asyncio.Queue = asyncio.Queue()
    store.attach_event_queue(session.session_id, queue)
    assert session.event_queue is queue


def test_attach_event_queue_rejects_unknown_session_id() -> None:
    store = SessionStore()
    queue: asyncio.Queue = asyncio.Queue()
    with pytest.raises(ValueError, match="unknown session_id"):
        store.attach_event_queue("nope", queue)


# ---------------------------------------------------------------------------
# heartbeat
# ---------------------------------------------------------------------------


async def test_heartbeat_updates_last_heartbeat() -> None:
    store = SessionStore()
    session = store.open("project", {})
    initial = session.last_heartbeat
    # Sleep a tick so the new timestamp is provably newer.
    await asyncio.sleep(0.01)
    store.heartbeat(session.session_id)
    assert session.last_heartbeat > initial


def test_heartbeat_is_noop_for_unknown_session() -> None:
    store = SessionStore()
    # Should not raise.
    store.heartbeat("does-not-exist")


# ---------------------------------------------------------------------------
# abandoned_older_than
# ---------------------------------------------------------------------------


def test_abandoned_older_than_returns_only_input_required_sessions() -> None:
    store = SessionStore()
    s_input = store.open("project", {})
    for state in (
        SessionState.VALIDATING,
        SessionState.RENDERING,
        SessionState.PLUGIN_PASS,
        SessionState.INPUT_REQUIRED,
    ):
        store.transition(s_input.session_id, state)
    # Force an old heartbeat.
    s_input.last_heartbeat = datetime.now(tz=UTC) - timedelta(hours=2)

    s_running = store.open("project", {})
    store.transition(s_running.session_id, SessionState.VALIDATING)
    s_running.last_heartbeat = datetime.now(tz=UTC) - timedelta(hours=2)

    abandoned = store.abandoned_older_than(timedelta(hours=1))
    assert s_input.session_id in abandoned
    assert s_running.session_id not in abandoned


def test_abandoned_older_than_skips_recent_input_required_sessions() -> None:
    store = SessionStore()
    s = store.open("project", {})
    for state in (
        SessionState.VALIDATING,
        SessionState.RENDERING,
        SessionState.PLUGIN_PASS,
        SessionState.INPUT_REQUIRED,
    ):
        store.transition(s.session_id, state)
    # Heartbeat is fresh (now); the threshold is 1 hour ago -> not abandoned.
    abandoned = store.abandoned_older_than(timedelta(hours=1))
    assert s.session_id not in abandoned


def test_abandoned_older_than_returns_empty_when_no_sessions_exist() -> None:
    store = SessionStore()
    assert store.abandoned_older_than(timedelta(hours=1)) == []


# ---------------------------------------------------------------------------
# close
# ---------------------------------------------------------------------------


def test_close_stores_outcome_for_done_session() -> None:
    store = SessionStore()
    session = store.open("project", {})
    for state in (
        SessionState.VALIDATING,
        SessionState.RENDERING,
        SessionState.PLUGIN_PASS,
        SessionState.CACHE_WRITE,
        SessionState.POST_VALIDATE,
        SessionState.SYNC_QUEUED,
        SessionState.DONE,
    ):
        store.transition(session.session_id, state)
    outcome = {"path": "/x", "sync_status": "pending"}
    store.close(session.session_id, outcome)
    assert session.result == outcome
    assert session.error is None


def test_close_stores_outcome_in_error_for_failed_session() -> None:
    store = SessionStore()
    session = store.open("project", {})
    store.transition(session.session_id, SessionState.FAILED)
    outcome = {"code": "validation_failed", "message": "bad"}
    store.close(session.session_id, outcome)
    assert session.error == outcome
    assert session.result is None


def test_close_is_noop_for_unknown_session() -> None:
    store = SessionStore()
    # Should not raise.
    store.close("nope", {"code": "x"})


# ---------------------------------------------------------------------------
# gc_loop
# ---------------------------------------------------------------------------


async def test_gc_loop_closes_abandoned_input_required_session() -> None:
    """The GC pass moves abandoned ``INPUT_REQUIRED`` sessions to ``ABORTED``."""
    store = SessionStore()
    session = store.open("project", {})
    for state in (
        SessionState.VALIDATING,
        SessionState.RENDERING,
        SessionState.PLUGIN_PASS,
        SessionState.INPUT_REQUIRED,
    ):
        store.transition(session.session_id, state)
    # Force the heartbeat to be older than the GC threshold.
    session.last_heartbeat = datetime.now(tz=UTC) - timedelta(hours=2)

    # Run a single GC pass directly so the test does not need to wait
    # the 5-minute interval. We use the threshold the loop applies
    # (1 hour) explicitly.
    store._gc_once(timedelta(hours=1))
    assert session.state is SessionState.ABORTED
    assert session.error is None  # close stored under ``result`` because not FAILED.
    assert session.result is not None
    assert session.result.get("code") == "session_abandoned"


async def test_gc_loop_periodically_runs_and_can_be_cancelled() -> None:
    """The ``gc_loop`` runs forever until cancelled."""
    store = SessionStore()
    # Start the loop with a very short interval so the test runs fast.
    task = asyncio.create_task(store.gc_loop(interval_seconds=0.01))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_gc_loop_skips_non_input_required_sessions() -> None:
    """Even with a stale heartbeat, only INPUT_REQUIRED sessions are GC'd."""
    store = SessionStore()
    session = store.open("project", {})
    store.transition(session.session_id, SessionState.VALIDATING)
    session.last_heartbeat = datetime.now(tz=UTC) - timedelta(hours=10)

    store._gc_once(timedelta(hours=1))
    assert session.state is SessionState.VALIDATING


# ---------------------------------------------------------------------------
# Session.is_terminal
# ---------------------------------------------------------------------------


def test_session_is_terminal_for_terminal_states() -> None:
    s = Session(
        session_id="x",
        kind="project",
        state=SessionState.DONE,
        request={},
        created_at=datetime.now(tz=UTC),
        last_heartbeat=datetime.now(tz=UTC),
    )
    assert s.is_terminal()
    s.state = SessionState.FAILED
    assert s.is_terminal()
    s.state = SessionState.ABORTED
    assert s.is_terminal()


def test_session_is_terminal_false_for_non_terminal_states() -> None:
    s = Session(
        session_id="x",
        kind="project",
        state=SessionState.PLUGIN_PASS,
        request={},
        created_at=datetime.now(tz=UTC),
        last_heartbeat=datetime.now(tz=UTC),
    )
    assert s.is_terminal() is False
