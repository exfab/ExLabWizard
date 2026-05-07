"""Bandwidth schedule editor (Frontend Spec §7.7.3).

Lives inside the Equipment Add/Edit sub-dialog. Two modes:

* **Unlimited** -- no cap, no schedule.
* **Limit upload bandwidth** -- a default cap (Mbps) plus zero-or-more
  schedule windows (Days, From, To, Upload Mbps).

Validation:

* Each row requires ``From < To``.
* Rows whose Days overlap each other render a non-blocking warning.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from exlab_wizard.logging import get_logger

_log = get_logger(__name__)

MODE_UNLIMITED = "unlimited"
MODE_LIMIT = "limit"

DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


@dataclass
class ScheduleWindow:
    """One row in the schedule table."""

    days: list[str] = field(default_factory=list)
    from_time: str = "08:00"
    to_time: str = "18:00"
    upload_mbps: int | None = None  # None = unlimited within window


@dataclass
class BandwidthSchedule:
    """The full editor state."""

    mode: str = MODE_UNLIMITED
    default_upload_mbps: int | None = None
    windows: list[ScheduleWindow] = field(default_factory=list)


def validate_window(window: ScheduleWindow) -> str | None:
    """Return an error string if ``window`` is invalid, else ``None``.

    Time strings are compared lexicographically because we use 24-hour
    HH:MM strings (sortable by character order).
    """

    if window.from_time >= window.to_time:
        return f"From ({window.from_time}) must be earlier than To ({window.to_time})"
    if window.upload_mbps is not None and window.upload_mbps < 0:
        return "Upload (Mbps) must be non-negative"
    return None


def find_overlaps(windows: list[ScheduleWindow]) -> list[tuple[int, int]]:
    """Return pairs of indices whose Days and time ranges overlap.

    Pairs are returned in canonical order ``(i, j)`` with ``i < j``.
    """

    overlaps: list[tuple[int, int]] = []
    for i, a in enumerate(windows):
        for j in range(i + 1, len(windows)):
            b = windows[j]
            if not set(a.days) & set(b.days):
                continue
            if a.to_time <= b.from_time or b.to_time <= a.from_time:
                continue
            overlaps.append((i, j))
    return overlaps


def schedule_props(schedule: BandwidthSchedule) -> dict[str, Any]:
    """Compute renderable props for a :class:`BandwidthSchedule`."""

    return {
        "mode": schedule.mode,
        "default_upload_mbps": schedule.default_upload_mbps,
        "windows": [
            {
                "days": list(w.days),
                "from_time": w.from_time,
                "to_time": w.to_time,
                "upload_mbps": w.upload_mbps,
                "error": validate_window(w),
            }
            for w in schedule.windows
        ],
        "overlaps": find_overlaps(schedule.windows),
    }


def bandwidth_schedule_editor(schedule: BandwidthSchedule) -> Any:
    """Build the schedule editor."""

    props = schedule_props(schedule)
    try:
        from nicegui import ui
    except Exception:
        return props

    column = ui.column().classes("w-full").style("gap: 0.5rem;")
    with column:
        ui.label("Bandwidth schedule").style(
            "font-family: var(--font-mono); "
            "font-size: var(--text-xs); "
            "letter-spacing: 0.08em; "
            "text-transform: uppercase; "
            "color: var(--color-muted);"
        )
        with ui.row().classes("items-center"):
            ui.radio(
                ["Unlimited", "Limit upload bandwidth"],
                value=(
                    "Unlimited" if schedule.mode == MODE_UNLIMITED else "Limit upload bandwidth"
                ),
            )
        if schedule.mode == MODE_LIMIT:
            ui.number(
                label="Default upload (Mbps)",
                value=schedule.default_upload_mbps,
            )
            for _idx, window in enumerate(schedule.windows):
                with ui.row().classes("items-center w-full"):
                    ui.label(",".join(window.days) or "(no days)")
                    ui.label(window.from_time)
                    ui.label(window.to_time)
                    ui.label(
                        "unlimited" if window.upload_mbps is None else f"{window.upload_mbps} Mbps",
                    )
                    err = validate_window(window)
                    if err:
                        ui.label(err).style("color: var(--color-danger);")
            for i, j in props["overlaps"]:
                ui.label(
                    f"Window {i + 1} overlaps window {j + 1}",
                ).style("color: var(--color-warning);")
    return column
