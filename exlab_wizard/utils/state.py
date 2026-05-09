"""Generic forward-transition guard for closed-set state machines.

Used by both the session-state machine (Backend Spec §4.7) and the NAS
ingest-state machine (Backend Spec §13.3) so a transition that the
caller did not intend is rejected at the source rather than corrupting
the persisted state.
"""

from __future__ import annotations

__all__ = ["assert_forward_transition"]


def assert_forward_transition[S](
    current: S,
    new_state: S,
    table: dict[S, frozenset[S]],
) -> None:
    """Raise ``ValueError`` when ``current -> new_state`` is not in ``table``.

    ``table`` maps each known source state to the frozenset of allowed
    destination states (an empty frozenset means the source is a
    terminal state). An unknown ``current`` raises immediately so a
    caller cannot silently skip the gate by passing an out-of-set
    sentinel.
    """
    allowed = table.get(current)
    if allowed is None:
        raise ValueError(f"unknown source state {current!r}")
    if new_state not in allowed:
        allowed_repr = sorted(str(s) for s in allowed) or "[terminal]"
        raise ValueError(
            f"illegal state transition {current!r} -> {new_state!r} "
            f"(allowed: {allowed_repr})"
        )
