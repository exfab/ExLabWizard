"""Tests for ``exlab_wizard.controller.state_machine``.

The state machine is the canonical encoding of the Backend Spec §4.7
diagram and the §4.7.1 mapping table; these tests pin every value so
spec drift triggers a test failure rather than silent breakage.
"""

from __future__ import annotations

import pytest

from exlab_wizard.controller.state_machine import (
    VALID_TRANSITIONS,
    Phase,
    SessionState,
    assert_transition,
    state_to_phase,
)

# ---------------------------------------------------------------------------
# state_to_phase mapping (§4.7.1 verbatim)
# ---------------------------------------------------------------------------


def test_state_to_phase_pending_returns_none() -> None:
    assert state_to_phase(SessionState.PENDING) is None


def test_state_to_phase_validating_returns_validating_inputs() -> None:
    assert state_to_phase(SessionState.VALIDATING) is Phase.VALIDATING_INPUTS


def test_state_to_phase_rendering_returns_rendering_template() -> None:
    assert state_to_phase(SessionState.RENDERING) is Phase.RENDERING_TEMPLATE


def test_state_to_phase_plugin_pass_returns_running_plugins() -> None:
    assert state_to_phase(SessionState.PLUGIN_PASS) is Phase.RUNNING_PLUGINS


def test_state_to_phase_input_required_returns_input_required() -> None:
    assert state_to_phase(SessionState.INPUT_REQUIRED) is Phase.INPUT_REQUIRED


def test_state_to_phase_cache_write_returns_writing_cache() -> None:
    assert state_to_phase(SessionState.CACHE_WRITE) is Phase.WRITING_CACHE


def test_state_to_phase_post_validate_returns_validating_post_creation() -> None:
    assert state_to_phase(SessionState.POST_VALIDATE) is Phase.VALIDATING_POST_CREATION


def test_state_to_phase_sync_queued_returns_queueing_nas_sync() -> None:
    assert state_to_phase(SessionState.SYNC_QUEUED) is Phase.QUEUEING_NAS_SYNC


def test_state_to_phase_done_returns_done() -> None:
    assert state_to_phase(SessionState.DONE) is Phase.DONE


def test_state_to_phase_failed_returns_none() -> None:
    assert state_to_phase(SessionState.FAILED) is None


def test_state_to_phase_aborted_returns_none() -> None:
    assert state_to_phase(SessionState.ABORTED) is None


def test_state_to_phase_covers_every_state() -> None:
    """Every ``SessionState`` value must have an explicit mapping entry."""
    for state in SessionState:
        # Should not raise KeyError; some return None.
        result = state_to_phase(state)
        assert result is None or isinstance(result, Phase)


# ---------------------------------------------------------------------------
# Forward-path transitions
# ---------------------------------------------------------------------------


def test_pending_transitions_to_validating() -> None:
    assert SessionState.VALIDATING in VALID_TRANSITIONS[SessionState.PENDING]


def test_validating_transitions_to_rendering() -> None:
    assert SessionState.RENDERING in VALID_TRANSITIONS[SessionState.VALIDATING]


def test_rendering_transitions_to_plugin_pass() -> None:
    assert SessionState.PLUGIN_PASS in VALID_TRANSITIONS[SessionState.RENDERING]


def test_plugin_pass_transitions_to_input_required_and_cache_write() -> None:
    allowed = VALID_TRANSITIONS[SessionState.PLUGIN_PASS]
    assert SessionState.INPUT_REQUIRED in allowed
    assert SessionState.CACHE_WRITE in allowed


def test_input_required_transitions_back_to_plugin_pass() -> None:
    assert SessionState.PLUGIN_PASS in VALID_TRANSITIONS[SessionState.INPUT_REQUIRED]


def test_cache_write_transitions_to_post_validate() -> None:
    assert SessionState.POST_VALIDATE in VALID_TRANSITIONS[SessionState.CACHE_WRITE]


def test_post_validate_transitions_to_sync_queued() -> None:
    assert SessionState.SYNC_QUEUED in VALID_TRANSITIONS[SessionState.POST_VALIDATE]


def test_sync_queued_transitions_to_done() -> None:
    assert SessionState.DONE in VALID_TRANSITIONS[SessionState.SYNC_QUEUED]


# ---------------------------------------------------------------------------
# Cancel / fail allowed from every non-terminal state
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "state",
    [
        SessionState.PENDING,
        SessionState.VALIDATING,
        SessionState.RENDERING,
        SessionState.PLUGIN_PASS,
        SessionState.INPUT_REQUIRED,
        SessionState.CACHE_WRITE,
        SessionState.POST_VALIDATE,
        SessionState.SYNC_QUEUED,
    ],
)
def test_fail_allowed_from_every_non_terminal_state(state: SessionState) -> None:
    assert SessionState.FAILED in VALID_TRANSITIONS[state]


@pytest.mark.parametrize(
    "state",
    [
        SessionState.PENDING,
        SessionState.VALIDATING,
        SessionState.RENDERING,
        SessionState.PLUGIN_PASS,
        SessionState.INPUT_REQUIRED,
        SessionState.CACHE_WRITE,
        SessionState.POST_VALIDATE,
        SessionState.SYNC_QUEUED,
    ],
)
def test_aborted_allowed_from_every_non_terminal_state(state: SessionState) -> None:
    assert SessionState.ABORTED in VALID_TRANSITIONS[state]


# ---------------------------------------------------------------------------
# Terminal states are sinks
# ---------------------------------------------------------------------------


def test_done_is_terminal() -> None:
    assert VALID_TRANSITIONS[SessionState.DONE] == frozenset()


def test_failed_is_terminal() -> None:
    assert VALID_TRANSITIONS[SessionState.FAILED] == frozenset()


def test_aborted_is_terminal() -> None:
    assert VALID_TRANSITIONS[SessionState.ABORTED] == frozenset()


# ---------------------------------------------------------------------------
# assert_transition guard
# ---------------------------------------------------------------------------


def test_assert_transition_allows_legal_forward_edge() -> None:
    # Should not raise.
    assert_transition(SessionState.PENDING, SessionState.VALIDATING)
    assert_transition(SessionState.VALIDATING, SessionState.RENDERING)


def test_assert_transition_allows_cancel_from_any_non_terminal() -> None:
    assert_transition(SessionState.PLUGIN_PASS, SessionState.ABORTED)
    assert_transition(SessionState.RENDERING, SessionState.FAILED)


def test_assert_transition_rejects_skipping_a_state() -> None:
    with pytest.raises(ValueError, match="illegal state transition"):
        assert_transition(SessionState.PENDING, SessionState.RENDERING)


def test_assert_transition_rejects_terminal_to_anything() -> None:
    with pytest.raises(ValueError, match="illegal state transition"):
        assert_transition(SessionState.DONE, SessionState.FAILED)


def test_assert_transition_rejects_done_back_to_pipeline() -> None:
    with pytest.raises(ValueError, match="illegal state transition"):
        assert_transition(SessionState.DONE, SessionState.VALIDATING)


def test_assert_transition_rejects_backwards_through_pipeline() -> None:
    with pytest.raises(ValueError, match="illegal state transition"):
        assert_transition(SessionState.RENDERING, SessionState.PENDING)


# ---------------------------------------------------------------------------
# Enum invariants
# ---------------------------------------------------------------------------


def test_session_state_values_match_lowercase_names() -> None:
    for state in SessionState:
        assert state.value == state.name.lower()


def test_phase_values_use_spec_strings() -> None:
    assert Phase.VALIDATING_INPUTS.value == "validating_inputs"
    assert Phase.RENDERING_TEMPLATE.value == "rendering_template"
    assert Phase.RUNNING_PLUGINS.value == "running_plugins"
    assert Phase.INPUT_REQUIRED.value == "input_required"
    assert Phase.WRITING_CACHE.value == "writing_cache"
    assert Phase.VALIDATING_POST_CREATION.value == "validating_post_creation"
    assert Phase.QUEUEING_NAS_SYNC.value == "queueing_nas_sync"
    assert Phase.DONE.value == "done"


def test_valid_transitions_keys_cover_every_session_state() -> None:
    assert set(VALID_TRANSITIONS) == set(SessionState)


def test_assert_transition_rejects_unknown_source_state() -> None:
    """The guard rejects a state not present in ``VALID_TRANSITIONS``.

    We can't easily construct a fake :class:`SessionState`, but we can
    monkeypatch ``VALID_TRANSITIONS`` and pass an enum we removed.
    """
    # Simpler: pass a current state whose mapping has been temporarily
    # cleared to simulate an unknown source.
    original = VALID_TRANSITIONS.pop(SessionState.PENDING)
    try:
        with pytest.raises(ValueError, match="unknown source state"):
            assert_transition(SessionState.PENDING, SessionState.VALIDATING)
    finally:
        VALID_TRANSITIONS[SessionState.PENDING] = original
