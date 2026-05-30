"""Unit tests for the footer status-bar segment derivation (Phase 5).

The :func:`derive_footer_segment_states` helper maps live backend counts to
per-segment ``SEGMENT_*`` states. It is pure, so the mapping is asserted here
without spinning up NiceGUI; the footer renderer reads its result and the
``status_bar_segment`` component itself is unchanged.
"""

from __future__ import annotations

from exlab_wizard.ui.components.status_bar_segment import (
    SEGMENT_DANGER,
    SEGMENT_NORMAL,
    SEGMENT_WARNING,
    derive_footer_segment_states,
)


def test_footer_segments_all_clear_default_normal() -> None:
    """No findings, LIMS reachable, nothing staged -> every segment normal."""

    states = derive_footer_segment_states(
        problems_count_hard=0,
        lims_reachable=True,
        staging_pending=0,
    )
    assert states.validator == SEGMENT_NORMAL
    assert states.lims == SEGMENT_NORMAL
    assert states.staging == SEGMENT_NORMAL


def test_footer_validator_warns_on_hard_findings() -> None:
    """A hard-tier finding flips Validator to WARNING (Frontend §3.5.5)."""

    states = derive_footer_segment_states(problems_count_hard=3, lims_reachable=True)
    assert states.validator == SEGMENT_WARNING
    # The other segments are unaffected by the validator count.
    assert states.lims == SEGMENT_NORMAL
    assert states.staging == SEGMENT_NORMAL


def test_footer_lims_danger_when_unreachable() -> None:
    """An unreachable LIMS endpoint flips LIMS to DANGER."""

    states = derive_footer_segment_states(problems_count_hard=0, lims_reachable=False)
    assert states.lims == SEGMENT_DANGER
    assert states.validator == SEGMENT_NORMAL


def test_footer_staging_warns_when_pending() -> None:
    """Runs pending clearance flip Staging to WARNING."""

    states = derive_footer_segment_states(
        problems_count_hard=0, lims_reachable=True, staging_pending=2
    )
    assert states.staging == SEGMENT_WARNING


def test_footer_segments_defaults_are_safe() -> None:
    """Called with no args (a half-wired backend) every segment is normal."""

    states = derive_footer_segment_states()
    assert (states.validator, states.lims, states.staging) == (
        SEGMENT_NORMAL,
        SEGMENT_NORMAL,
        SEGMENT_NORMAL,
    )
