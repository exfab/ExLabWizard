"""Bottom-of-window status bar segment (Frontend Spec §3.5.5).

The status bar has three segments left-to-right -- Sync, Validator, LIMS --
each clickable and using ``var(--text-xs)`` monospace.

The component handles two concerns:

* **Color logic** -- normal state uses ``var(--color-muted)``; warning
  states use ``var(--color-warning)``; error states use
  ``var(--color-danger)``.
* **Layout** -- a small rounded segment with optional warning glyph
  prefix.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from exlab_wizard.logging import get_logger

_log = get_logger(__name__)


SEGMENT_NORMAL = "normal"
SEGMENT_WARNING = "warning"
SEGMENT_DANGER = "danger"


@dataclass(frozen=True)
class FooterSegmentStates:
    """Derived ``SEGMENT_*`` state for each footer status segment (Phase 5).

    Only the segments whose state varies with live backend signals are
    carried here -- Validator, LIMS, Staging. The Sync segment keeps its own
    richer label/click logic in the page renderer (it doubles as the
    Operations entry point), so it is not duplicated in this mapping.
    """

    validator: str
    lims: str
    staging: str


def derive_footer_segment_states(
    *,
    problems_count_hard: int = 0,
    lims_reachable: bool = True,
    staging_pending: int = 0,
) -> FooterSegmentStates:
    """Map live backend counts to per-segment states (Frontend §3.5.5).

    * **Validator** -- ``WARNING`` when at least one hard-tier finding gates
      work (``problems_count_hard > 0``), sourced from the 30 s background
      audit rollup.
    * **LIMS** -- ``DANGER`` when the LIMS endpoint is unreachable
      (``deps.lims_reachable`` is ``False``).
    * **Staging** -- ``WARNING`` when runs are pending clearance. There is no
      cheap cached count today (a per-render ``list_staged_runs`` scan would
      be I/O on the render path), so the caller passes ``0`` and the segment
      stays ``NORMAL`` until a cached signal exists; the parameter is wired so
      that becomes a one-line change.

    Pure so the mapping is unit-testable; the defaults describe a clean /
    half-wired backend (every segment ``NORMAL``) so a missing signal degrades
    quietly rather than raising.
    """
    return FooterSegmentStates(
        validator=SEGMENT_WARNING if problems_count_hard > 0 else SEGMENT_NORMAL,
        lims=SEGMENT_NORMAL if lims_reachable else SEGMENT_DANGER,
        staging=SEGMENT_WARNING if staging_pending > 0 else SEGMENT_NORMAL,
    )


@dataclass(frozen=True)
class SegmentSpec:
    """Computed render spec for a status-bar segment."""

    label: str
    state: str
    color_var: str
    show_warning_glyph: bool


def segment_spec(
    *,
    label: str,
    state: str = SEGMENT_NORMAL,
) -> SegmentSpec:
    """Compute a :class:`SegmentSpec` from a label and state.

    The state controls the color and whether a warning glyph prefixes the
    label.
    """

    if state == SEGMENT_WARNING:
        color = "--color-warning"
        prefix = True
    elif state == SEGMENT_DANGER:
        color = "--color-danger"
        prefix = True
    else:
        color = "--color-muted"
        prefix = False
    return SegmentSpec(label=label, state=state, color_var=color, show_warning_glyph=prefix)


def status_bar_segment(
    *,
    label: str,
    state: str = SEGMENT_NORMAL,
    on_click: Callable[[], None] | None = None,
) -> Any:
    """Build a clickable status-bar segment."""

    spec = segment_spec(label=label, state=state)
    try:
        from nicegui import ui
    except Exception:
        return spec

    segment = (
        ui.row()
        .classes("items-center cursor-pointer")
        .style(
            f"color: var({spec.color_var}); "
            "padding: 0 0.75rem; "
            "font-family: var(--font-mono); "
            "font-size: var(--text-xs); "
            "gap: 0.25rem;"
        )
    )
    with segment:
        if spec.show_warning_glyph:
            ui.label("⚠").style("font-size: var(--text-sm);")
        ui.label(spec.label)
    if on_click is not None:
        segment.on("click", lambda _evt: on_click())
    return segment
