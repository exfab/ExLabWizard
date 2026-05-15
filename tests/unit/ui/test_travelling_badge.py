"""Unit tests for the travelling problem-badge pure function.

GUI/Orchestrator Redesign §4.5: the badge sits on the shallowest
collapsed ancestor of each finding and travels inward as nodes expand.
Red beats amber when a node aggregates both.
"""

from __future__ import annotations

from exlab_wizard.ui.components.travelling_badge import (
    BadgeProps,
    FindingLocation,
    travelling_badges,
)


def _hard(path: str) -> FindingLocation:
    return FindingLocation(path=path, tier="hard")


def _soft(path: str) -> FindingLocation:
    return FindingLocation(path=path, tier="soft")


def test_badge_on_root_when_all_collapsed() -> None:
    """All collapsed: the badge lives on the shallowest collapsed
    ancestor — the root equipment node."""
    findings = [_hard("EQ_A/PROJ-0001/Runs/Run_2026-05-14T09-22")]
    fold = {}  # everything collapsed
    badges = travelling_badges(findings, fold)
    assert badges == {"EQ_A": BadgeProps(color="red", count=1)}


def test_badge_travels_inward_as_nodes_expand() -> None:
    """Expanding the equipment node moves the badge to the project."""
    findings = [_hard("EQ_A/PROJ-0001/Runs/Run_2026-05-14T09-22")]
    badges = travelling_badges(findings, {"EQ_A": True})
    assert badges == {"EQ_A/PROJ-0001": BadgeProps(color="red", count=1)}


def test_badge_lands_on_leaf_when_fully_expanded() -> None:
    findings = [_hard("EQ_A/PROJ-0001/Runs/Run_2026-05-14T09-22")]
    fold = {
        "EQ_A": True,
        "EQ_A/PROJ-0001": True,
        "EQ_A/PROJ-0001/Runs": True,
    }
    badges = travelling_badges(findings, fold)
    assert badges == {
        "EQ_A/PROJ-0001/Runs/Run_2026-05-14T09-22": BadgeProps(
            color="red", count=1
        )
    }


def test_red_beats_amber_when_aggregated_on_same_node() -> None:
    findings = [
        _hard("EQ_A/PROJ-0001/Runs/Run_x"),
        _soft("EQ_A/PROJ-0002/Runs/Run_y"),
    ]
    badges = travelling_badges(findings, {})
    # Both collapse onto EQ_A; total is 2 with red color.
    assert badges == {"EQ_A": BadgeProps(color="red", count=2)}


def test_amber_only_when_no_hard_findings() -> None:
    findings = [_soft("EQ_A/PROJ-0001/Runs/Run_x")]
    badges = travelling_badges(findings, {})
    assert badges == {"EQ_A": BadgeProps(color="amber", count=1)}


def test_two_equipment_get_independent_badges() -> None:
    findings = [
        _hard("EQ_A/PROJ-0001/Runs/Run_x"),
        _soft("EQ_B/PROJ-0002/Runs/Run_y"),
    ]
    badges = travelling_badges(findings, {})
    assert badges == {
        "EQ_A": BadgeProps(color="red", count=1),
        "EQ_B": BadgeProps(color="amber", count=1),
    }
