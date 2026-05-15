"""Creation-session state machine. Backend Spec §4.7, §4.7.1.

Defines two enums and the mapping/transition tables that govern a
``CreationController`` session's lifecycle:

- :class:`SessionState` -- the *internal* state the controller drives
  (Backend Spec §4.7). Includes terminal-error states (``FAILED``,
  ``ABORTED``) and the holding state ``INPUT_REQUIRED`` that emit no
  ``phase`` WebSocket frame on entry.
- :class:`Phase` -- the *externally-emitted* ``phase`` enum
  (Backend Spec §4.6.2). Sent over the ``WS /api/v1/sessions/{id}/events``
  channel on every state transition that has a corresponding phase event.
- :func:`state_to_phase` -- the §4.7.1 mapping table verbatim. Returns
  ``None`` for transitional / terminal-error states; returns
  :data:`Phase.INPUT_REQUIRED` for ``INPUT_REQUIRED`` (the API surface
  encodes this as a ``kind: "input_required"`` envelope rather than a
  ``phase`` frame -- the helper still emits the phase value so a single
  switch on ``state_to_phase`` can drive both code paths).
- :data:`VALID_TRANSITIONS` -- the per-state outbound transition table
  per the §4.7 diagram. ``FAILED`` and ``ABORTED`` are reachable from any
  non-terminal state (cancel/fail can fire mid-pipeline); the table is
  hand-written rather than generated to keep the spec ↔ code mapping
  auditable.
- :func:`assert_transition` -- guard used by :class:`SessionStore` and
  :class:`CreationController` to reject invalid state transitions
  (caught by the type system at the API surface; the runtime raise is
  defensive against logic bugs in the controller's pipeline).
"""

from __future__ import annotations

from enum import StrEnum

from exlab_wizard.utils.state import assert_forward_transition

__all__ = [
    "VALID_TRANSITIONS",
    "Phase",
    "SessionState",
    "assert_transition",
    "state_to_phase",
]


class SessionState(StrEnum):
    """Internal creation-session state. Backend Spec §4.7.

    Values mirror the §4.7 state-machine diagram. Lower-case strings so
    JSON encoding is direct (``StrEnum`` makes ``SessionState.PENDING``
    render as ``"pending"``).
    """

    PENDING = "pending"
    VALIDATING = "validating"
    RENDERING = "rendering"
    PLUGIN_PASS = "plugin_pass"
    INPUT_REQUIRED = "input_required"
    CACHE_WRITE = "cache_write"
    POST_VALIDATE = "post_validate"
    SYNC_QUEUED = "sync_queued"
    DONE = "done"
    FAILED = "failed"
    ABORTED = "aborted"


class Phase(StrEnum):
    """Externally-emitted ``phase`` event. Backend Spec §4.6.2.

    Sent over the ``WS /api/v1/sessions/{id}/events`` channel on every
    state transition that has a corresponding phase event. The enum
    values match the spec's wire-format strings verbatim.
    """

    VALIDATING_INPUTS = "validating_inputs"
    RENDERING_TEMPLATE = "rendering_template"
    RUNNING_PLUGINS = "running_plugins"
    INPUT_REQUIRED = "input_required"
    WRITING_CACHE = "writing_cache"
    VALIDATING_POST_CREATION = "validating_post_creation"
    QUEUEING_NAS_SYNC = "queueing_nas_sync"
    DONE = "done"


# Backend Spec §4.7.1 mapping table verbatim. Internal state names that
# emit no phase frame are mapped to ``None`` so callers can ``if phase
# is None: continue`` cleanly.
_STATE_TO_PHASE: dict[SessionState, Phase | None] = {
    SessionState.PENDING: None,
    SessionState.VALIDATING: Phase.VALIDATING_INPUTS,
    SessionState.RENDERING: Phase.RENDERING_TEMPLATE,
    SessionState.PLUGIN_PASS: Phase.RUNNING_PLUGINS,
    SessionState.INPUT_REQUIRED: Phase.INPUT_REQUIRED,
    SessionState.CACHE_WRITE: Phase.WRITING_CACHE,
    SessionState.POST_VALIDATE: Phase.VALIDATING_POST_CREATION,
    SessionState.SYNC_QUEUED: Phase.QUEUEING_NAS_SYNC,
    SessionState.DONE: Phase.DONE,
    SessionState.FAILED: None,
    SessionState.ABORTED: None,
}


def state_to_phase(state: SessionState) -> Phase | None:
    """Return the :class:`Phase` event corresponding to ``state``.

    Backend Spec §4.7.1 mapping table. ``PENDING``, ``FAILED``, and
    ``ABORTED`` return ``None`` (no phase event). ``INPUT_REQUIRED``
    returns :data:`Phase.INPUT_REQUIRED`; the API surface encodes
    that as a ``kind: "input_required"`` envelope rather than a
    ``phase`` frame, but the mapping is preserved so a single dispatch
    knows the relationship.
    """
    return _STATE_TO_PHASE[state]


# Terminal states from which no further transition is legal.
_TERMINAL_STATES: frozenset[SessionState] = frozenset(
    {SessionState.DONE, SessionState.FAILED, SessionState.ABORTED}
)


# Spec §4.7 transition diagram. ``FAILED`` and ``ABORTED`` are added
# below so any non-terminal state can fail / cancel without a per-state
# enumeration. ``DONE`` is reachable only from ``SYNC_QUEUED``.
_FORWARD_TRANSITIONS: dict[SessionState, frozenset[SessionState]] = {
    SessionState.PENDING: frozenset({SessionState.VALIDATING}),
    SessionState.VALIDATING: frozenset({SessionState.RENDERING}),
    SessionState.RENDERING: frozenset({SessionState.PLUGIN_PASS}),
    SessionState.PLUGIN_PASS: frozenset({SessionState.INPUT_REQUIRED, SessionState.CACHE_WRITE}),
    SessionState.INPUT_REQUIRED: frozenset({SessionState.PLUGIN_PASS}),
    SessionState.CACHE_WRITE: frozenset({SessionState.POST_VALIDATE}),
    SessionState.POST_VALIDATE: frozenset({SessionState.SYNC_QUEUED}),
    SessionState.SYNC_QUEUED: frozenset({SessionState.DONE}),
    SessionState.DONE: frozenset(),
    SessionState.FAILED: frozenset(),
    SessionState.ABORTED: frozenset(),
}


def _build_valid_transitions() -> dict[SessionState, frozenset[SessionState]]:
    """Augment forward transitions with cancel/fail edges."""
    result: dict[SessionState, frozenset[SessionState]] = {}
    cancel_targets = frozenset({SessionState.FAILED, SessionState.ABORTED})
    for state, forward in _FORWARD_TRANSITIONS.items():
        if state in _TERMINAL_STATES:
            result[state] = forward
        else:
            result[state] = forward | cancel_targets
    return result


# Per-state outbound transition table. Per the spec:
# - Forward edges follow the §4.7 diagram exactly.
# - ``cancel`` from any non-terminal state -> ``ABORTED``.
# - ``fail`` from any non-terminal state -> ``FAILED``.
# Terminal states (``DONE`` / ``FAILED`` / ``ABORTED``) are sinks.
VALID_TRANSITIONS: dict[SessionState, frozenset[SessionState]] = _build_valid_transitions()


def assert_transition(current: SessionState, new_state: SessionState) -> None:
    """Raise :class:`ValueError` if ``current -> new_state`` is illegal.

    Defensive guard used by :class:`SessionStore.transition` and the
    controller's pipeline. Backend Spec §4.7 / §4.7.1 are the source of
    truth for the legal edges; this function consults
    :data:`VALID_TRANSITIONS` via the shared
    :func:`exlab_wizard.utils.state.assert_forward_transition` helper.
    """
    assert_forward_transition(current, new_state, VALID_TRANSITIONS)
