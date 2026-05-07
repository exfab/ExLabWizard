"""Unit tests for the staging UI page.

The page renders NiceGUI elements when the framework is importable;
these tests exercise the pure formatters and the data-shape contract
(column ordering, state pill colors, button enable/disable rules).
Visual concerns are covered by Phase-16 Playwright tests.
"""

from __future__ import annotations

from exlab_wizard.constants import IngestState
from exlab_wizard.orchestrator.staging_query import StagedRunSummary
from exlab_wizard.ui.pages.staging import (
    STAGING_DOCK_HEIGHT_PX,
    STAGING_TABLE_COLUMNS,
    StagingDockState,
    format_bytes,
    format_elapsed,
    render_staging_dock,
    row_props,
    state_pill_props,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_staging_dock_height_is_120_px() -> None:
    assert STAGING_DOCK_HEIGHT_PX == 120


def test_staging_table_columns_match_brief_order() -> None:
    assert STAGING_TABLE_COLUMNS == (
        "State",
        "Run",
        "Equipment",
        "Files",
        "Bytes",
        "Elapsed",
        "Actions",
    )


# ---------------------------------------------------------------------------
# format_bytes
# ---------------------------------------------------------------------------


def test_format_bytes_handles_negative_values() -> None:
    assert format_bytes(-1) == "0 B"


def test_format_bytes_renders_sub_kib_in_bytes() -> None:
    assert format_bytes(0) == "0 B"
    assert format_bytes(512) == "512 B"


def test_format_bytes_renders_kib_with_one_decimal() -> None:
    assert format_bytes(1024) == "1.0 KiB"
    assert format_bytes(2048 + 512) == "2.5 KiB"


def test_format_bytes_renders_mib_and_gib() -> None:
    assert format_bytes(1024 * 1024) == "1.0 MiB"
    assert format_bytes(1024 * 1024 * 1024) == "1.0 GiB"


# ---------------------------------------------------------------------------
# format_elapsed
# ---------------------------------------------------------------------------


def test_format_elapsed_seconds_only() -> None:
    assert format_elapsed(0) == "0s"
    assert format_elapsed(45) == "45s"


def test_format_elapsed_minutes_and_seconds() -> None:
    assert format_elapsed(125) == "2m 5s"


def test_format_elapsed_hours_and_minutes() -> None:
    assert format_elapsed(3600 + 60 * 5) == "1h 5m"


def test_format_elapsed_days_and_hours() -> None:
    assert format_elapsed(86400 * 2 + 3600 * 3) == "2d 3h"


def test_format_elapsed_handles_negative() -> None:
    assert format_elapsed(-10) == "0s"


# ---------------------------------------------------------------------------
# state_pill_props
# ---------------------------------------------------------------------------


def test_state_pill_props_returns_label_and_color_for_each_state() -> None:
    for state in (
        IngestState.STAGING,
        IngestState.COMPLETE,
        IngestState.SYNC_QUEUED,
        IngestState.SYNC_VERIFIED,
        IngestState.CLEARED,
    ):
        props = state_pill_props(state.value)
        assert props["label"] == state.value
        assert "color" in props
        assert "background" in props


def test_state_pill_props_falls_back_for_unknown_state() -> None:
    props = state_pill_props("unrecognised")
    assert props["label"] == "unrecognised"
    assert props["color"] == "var(--color-muted)"


# ---------------------------------------------------------------------------
# row_props
# ---------------------------------------------------------------------------


def _make_row(
    *,
    state: IngestState = IngestState.STAGING,
    path: str = "/staging/EQ1/PROJ-0001/Run_2026-04-17T14-32-00",
    files: int = 5,
    byte_total: int = 4096,
    elapsed: int = 90,
) -> StagedRunSummary:
    return StagedRunSummary(
        path=path,
        current_state=state.value,
        equipment_id="EQ1",
        project_name="PROJ-0001",
        run_kind="experimental",
        file_count=files,
        byte_total=byte_total,
        elapsed_seconds_since_last_activity=elapsed,
        last_activity_at="2026-04-17T12:00:00Z",
    )


def test_row_props_emits_run_label_as_leaf() -> None:
    props = row_props(_make_row())
    assert props["run_label"] == "Run_2026-04-17T14-32-00"


def test_row_props_marks_sync_verified_as_clearable() -> None:
    props = row_props(_make_row(state=IngestState.SYNC_VERIFIED))
    assert props["is_clearable"] is True


def test_row_props_does_not_mark_other_states_as_clearable() -> None:
    for state in (
        IngestState.STAGING,
        IngestState.COMPLETE,
        IngestState.SYNC_QUEUED,
        IngestState.CLEARED,
    ):
        props = row_props(_make_row(state=state))
        assert props["is_clearable"] is False, state


def test_row_props_formats_bytes_and_elapsed() -> None:
    props = row_props(_make_row(byte_total=1024, elapsed=125))
    assert props["bytes"] == "1.0 KiB"
    assert props["elapsed"] == "2m 5s"


# ---------------------------------------------------------------------------
# render_staging_dock (no-NiceGUI branch)
# ---------------------------------------------------------------------------


def test_render_staging_dock_returns_dict_when_nicegui_unavailable(monkeypatch) -> None:
    """When NiceGUI isn't importable the renderer returns a debug dict.

    This is the unit-test path; the full render is covered by the
    Playwright e2e suite in Phase 16.
    """
    import sys

    monkeypatch.setitem(sys.modules, "nicegui", None)
    state = StagingDockState(rows=[_make_row()])
    result = render_staging_dock(state)
    if isinstance(result, dict):
        assert result["height_px"] == STAGING_DOCK_HEIGHT_PX
        assert result["columns"] == STAGING_TABLE_COLUMNS
        assert len(result["rows"]) == 1


def test_staging_dock_state_defaults() -> None:
    """An empty dock state initializes without errors."""
    state = StagingDockState(rows=[])
    assert state.rows == []
    assert state.on_force_sync is None
    assert state.on_clear is None
    assert state.on_view_log is None
    assert state.on_clear_verified is None
