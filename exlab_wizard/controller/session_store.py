"""In-memory session store + GC for creation sessions. Backend Spec §4.4.7.

The :class:`SessionStore` is a ``dict[session_id, Session]`` keyed by
UUID4. v1 is intentionally non-persistent: the store lives in the
long-lived tray-server process, and a server crash forfeits all
in-flight sessions (Backend Spec §4.8). Persistence may return in v2
when unattended workflows ship.

The GC pass closes any session in :data:`SessionState.INPUT_REQUIRED`
with no client heartbeat for >1 hour
(:data:`SESSION_GC_AFTER_SECONDS`); see Backend Spec §4.4.7.

The ``transition`` method is the single mutation surface for a
session's :class:`SessionState` and ``current_phase`` -- both fields
are updated atomically (under no-lock by virtue of the asyncio
single-threaded event loop) so the WebSocket subscriber sees a
consistent view.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from exlab_wizard.constants import SESSION_GC_AFTER_SECONDS
from exlab_wizard.controller.state_machine import (
    Phase,
    SessionState,
    assert_transition,
    state_to_phase,
)
from exlab_wizard.logging import get_logger

__all__ = ["Session", "SessionStore"]


_log = get_logger(__name__)


SessionKind = Literal["project", "run"]


@dataclass
class Session:
    """One creation session. Backend Spec §4.4.7.

    Attributes:
        session_id: UUID4 string assigned by the store on :meth:`open`.
        kind: ``"project"`` or ``"run"`` -- mirrors the controller's
            ``create_*`` entry point.
        state: Current :class:`SessionState`. Mutated only via
            :meth:`SessionStore.transition`.
        request: The original create request bundle
            (``ProjectCreateRequest`` or ``RunCreateRequest``).
        created_at: UTC timestamp at :meth:`SessionStore.open`.
        last_heartbeat: Most recent client-driven heartbeat. Refreshed
            by :meth:`SessionStore.heartbeat`; consulted by the GC.
        current_phase: Mirrors :func:`state_to_phase` of ``state``.
            Maintained by :meth:`SessionStore.transition`.
        next_action: ``"awaiting_input"`` while the session is in
            :data:`SessionState.INPUT_REQUIRED`; ``"none"`` otherwise.
        event_queue: WebSocket fan-out queue. Set by
            :meth:`SessionStore.attach_event_queue`.
        pending_input: Latest ``InputRequiredPayload`` dict surfaced by
            the plugin host; cleared on resume.
        error: Structured error envelope (``{code, message, ...}``) on
            failure. ``None`` while the session is in flight.
        result: Structured ``done`` payload at session close. ``None``
            while in flight or on failure.
    """

    session_id: str
    kind: SessionKind
    state: SessionState
    request: Any
    created_at: datetime
    last_heartbeat: datetime
    current_phase: Phase | None = None
    next_action: str = "none"
    event_queue: asyncio.Queue[dict[str, Any]] | None = None
    pending_input: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    result: dict[str, Any] | None = None

    def is_terminal(self) -> bool:
        """Return ``True`` if the session reached a terminal state."""
        return self.state in (SessionState.DONE, SessionState.FAILED, SessionState.ABORTED)


class SessionStore:
    """In-memory session store. Backend Spec §4.4.7.

    Sessions are keyed by UUID4 string; the dict is in-memory for v1
    (no persistence across server restarts -- Backend Spec §4.8).
    """

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    # ----- Lifecycle ---------------------------------------------------

    def open(self, kind: SessionKind, req: Any) -> Session:
        """Create a fresh session in :data:`SessionState.PENDING` state."""
        session_id = str(uuid.uuid4())
        now = datetime.now(tz=UTC)
        session = Session(
            session_id=session_id,
            kind=kind,
            state=SessionState.PENDING,
            request=req,
            created_at=now,
            last_heartbeat=now,
            current_phase=None,
            next_action="none",
        )
        self._sessions[session_id] = session
        _log.debug(
            "session opened",
            extra={"context": {"session_id": session_id, "kind": kind}},
        )
        return session

    def get(self, session_id: str) -> Session | None:
        """Return the session keyed by ``session_id``, or ``None``."""
        return self._sessions.get(session_id)

    def transition(self, session_id: str, new_state: SessionState) -> None:
        """Move ``session_id`` to ``new_state``, updating ``current_phase``.

        Validates the transition against
        :data:`exlab_wizard.controller.state_machine.VALID_TRANSITIONS`
        and raises :class:`ValueError` on illegal edges. ``next_action``
        is updated alongside ``state``: ``INPUT_REQUIRED`` -> ``"awaiting_input"``,
        every other state -> ``"none"``.
        """
        session = self._sessions.get(session_id)
        if session is None:
            raise ValueError(f"unknown session_id {session_id!r}")
        assert_transition(session.state, new_state)
        session.state = new_state
        session.current_phase = state_to_phase(new_state)
        session.next_action = (
            "awaiting_input" if new_state is SessionState.INPUT_REQUIRED else "none"
        )

    def attach_event_queue(self, session_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        """Attach a WebSocket fan-out queue to the session.

        The controller pushes WebSocket frames onto the queue; the
        ``WS /api/v1/sessions/{id}/events`` channel reads from it. One
        queue per session; re-attaching replaces the prior queue.
        """
        session = self._sessions.get(session_id)
        if session is None:
            raise ValueError(f"unknown session_id {session_id!r}")
        session.event_queue = queue

    def heartbeat(self, session_id: str) -> None:
        """Refresh ``last_heartbeat`` so the GC will not close this session.

        No-op when the session is unknown so a stale client does not
        crash the server.
        """
        session = self._sessions.get(session_id)
        if session is None:
            return
        session.last_heartbeat = datetime.now(tz=UTC)

    def close(self, session_id: str, outcome: dict[str, Any]) -> None:
        """Stamp a terminal-state session with the outcome envelope.

        ``outcome`` is the structured payload that the WebSocket
        ``done`` / ``failed`` frame carried. ``DONE`` outcomes go into
        ``result``; ``FAILED`` outcomes go into ``error``; ``ABORTED``
        sessions store the outcome under ``result`` so the operator can
        recover the partial-creation summary if the cancel was a
        deliberate abort.
        """
        session = self._sessions.get(session_id)
        if session is None:
            return
        if session.state is SessionState.FAILED:
            session.error = outcome
        else:
            session.result = outcome

    # ----- GC ----------------------------------------------------------

    def abandoned_older_than(self, age: timedelta) -> list[str]:
        """Return ids of :data:`SessionState.INPUT_REQUIRED` sessions
        whose ``last_heartbeat`` is older than ``age``.

        Used by :meth:`gc_loop` to identify sessions abandoned by their
        operator (no client heartbeat for the configured window). Only
        ``INPUT_REQUIRED`` sessions are eligible -- transient states
        are owned by the controller and finish on their own.
        """
        threshold = datetime.now(tz=UTC) - age
        return [
            sid
            for sid, session in self._sessions.items()
            if session.state is SessionState.INPUT_REQUIRED and session.last_heartbeat < threshold
        ]

    async def gc_loop(self, interval_seconds: float = 300.0) -> None:
        """Run the abandoned-session GC forever. Backend Spec §4.4.7.

        Sleeps ``interval_seconds`` between passes (default 5 min); on
        each wake closes every ``INPUT_REQUIRED`` session whose
        heartbeat is older than :data:`SESSION_GC_AFTER_SECONDS`
        (default 1 hour). Cancellation is honored cleanly: the loop
        catches :class:`asyncio.CancelledError` and re-raises so the
        caller's cancellation propagates.
        """
        gc_age = timedelta(seconds=SESSION_GC_AFTER_SECONDS)
        try:
            while True:
                await asyncio.sleep(interval_seconds)
                self._gc_once(gc_age)
        except asyncio.CancelledError:
            raise

    def _gc_once(self, gc_age: timedelta) -> None:
        """One GC pass: close every abandoned ``INPUT_REQUIRED`` session."""
        for session_id in self.abandoned_older_than(gc_age):
            session = self._sessions.get(session_id)
            if session is None:
                continue
            with suppress(ValueError):
                self.transition(session_id, SessionState.ABORTED)
            self.close(
                session_id,
                {"code": "session_abandoned", "reason": "no client heartbeat for >1h"},
            )
            _log.info(
                "session GC closed abandoned INPUT_REQUIRED session",
                extra={"context": {"session_id": session_id}},
            )
