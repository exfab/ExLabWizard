"""Unit tests for ``ui/components/breadcrumb``. Redesign §4.7 / decision 7A."""

from __future__ import annotations

from exlab_wizard.ui.components.breadcrumb import (
    BreadcrumbSegment,
    segments_from_node_id,
)


def test_segments_from_none_returns_empty() -> None:
    assert segments_from_node_id(None) == []


def test_segments_from_empty_returns_empty() -> None:
    assert segments_from_node_id("") == []


def test_segments_for_run_node() -> None:
    segs = segments_from_node_id("EQ_A/PROJ-0001/Runs/Run_2026-05-14T09-22")
    assert segs == [
        BreadcrumbSegment(label="EQ_A", node_id="EQ_A"),
        BreadcrumbSegment(label="PROJ-0001", node_id="EQ_A/PROJ-0001"),
        BreadcrumbSegment(label="Runs", node_id="EQ_A/PROJ-0001/Runs"),
        BreadcrumbSegment(
            label="Run_2026-05-14T09-22",
            node_id="EQ_A/PROJ-0001/Runs/Run_2026-05-14T09-22",
        ),
    ]


def test_segments_strip_empty_components() -> None:
    """A double-slash or trailing slash doesn't produce empty segments."""
    segs = segments_from_node_id("EQ_A//PROJ-0001/")
    assert segs == [
        BreadcrumbSegment(label="EQ_A", node_id="EQ_A"),
        BreadcrumbSegment(label="PROJ-0001", node_id="EQ_A/PROJ-0001"),
    ]
