"""Tests for ``exlab_wizard.utils.state``."""

from __future__ import annotations

from enum import StrEnum

import pytest

from exlab_wizard.utils.state import assert_forward_transition


class _State(StrEnum):
    A = "a"
    B = "b"
    C = "c"


_TABLE: dict[_State, frozenset[_State]] = {
    _State.A: frozenset({_State.B}),
    _State.B: frozenset({_State.C}),
    _State.C: frozenset(),  # terminal
}


def test_allowed_transition_returns_none() -> None:
    assert assert_forward_transition(_State.A, _State.B, _TABLE) is None


def test_disallowed_transition_raises() -> None:
    with pytest.raises(ValueError, match="illegal state transition"):
        assert_forward_transition(_State.A, _State.C, _TABLE)


def test_terminal_transition_raises() -> None:
    with pytest.raises(ValueError, match="terminal"):
        assert_forward_transition(_State.C, _State.A, _TABLE)


def test_unknown_source_raises() -> None:
    class _Other(StrEnum):
        Z = "z"

    with pytest.raises(ValueError, match="unknown source state"):
        assert_forward_transition(_Other.Z, _State.A, _TABLE)
