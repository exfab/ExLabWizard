"""Filter-chip strip component (Frontend Spec §3.5.4, §11.1).

A row of toggleable chips with optional default-on / default-off state.
Used in:

* The main-window left tree (Active default-on, Archived default-off,
  Test runs default-on; Frontend §3.5.4).
* The Problems tab header (Severity, Class, State, Scope; Frontend §11.1).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from exlab_wizard.logging import get_logger

_log = get_logger(__name__)


@dataclass(frozen=True)
class ChipDefinition:
    """One chip in a chip group."""

    chip_id: str
    label: str
    default_on: bool


@dataclass
class ChipState:
    """Mutable state for a chip group."""

    active: set[str]


def initial_state(chips: Sequence[ChipDefinition]) -> ChipState:
    """Build a :class:`ChipState` from each chip's ``default_on``."""

    return ChipState(active={c.chip_id for c in chips if c.default_on})


def toggle(state: ChipState, chip_id: str) -> ChipState:
    """Flip the state of ``chip_id`` and return the new :class:`ChipState`."""

    new_active = state.active - {chip_id} if chip_id in state.active else state.active | {chip_id}
    return ChipState(active=new_active)


def is_active(state: ChipState, chip_id: str) -> bool:
    """Return ``True`` when ``chip_id`` is currently toggled on."""

    return chip_id in state.active


def list_active(state: ChipState, chips: Iterable[ChipDefinition]) -> list[str]:
    """Return the ids of currently-active chips, preserving definition order."""

    return [c.chip_id for c in chips if c.chip_id in state.active]


def filter_chips(
    chips: Sequence[ChipDefinition],
    *,
    on_change: Callable[[ChipState], None] | None = None,
    state: ChipState | None = None,
) -> Any:
    """Build the chip strip.

    Returns the NiceGUI row, or the immutable props dict in test contexts.
    """

    current = state or initial_state(chips)
    payload = {
        "chips": [c.__dict__ for c in chips],
        "active": sorted(current.active),
    }

    try:
        from nicegui import ui
    except Exception:
        return payload

    row = ui.row().classes("items-center").style("gap: 0.5rem; flex-wrap: wrap;")
    with row:
        for chip in chips:

            def _make_handler(cid: str) -> Callable[[Any], None]:
                def _handler(_evt: Any) -> None:
                    nonlocal current
                    current = toggle(current, cid)
                    if on_change is not None:
                        on_change(current)

                return _handler

            ui_chip = ui.chip(
                chip.label,
                on_click=_make_handler(chip.chip_id),
            )
            if is_active(current, chip.chip_id):
                ui_chip.props("color=primary text-color=white")
            else:
                ui_chip.props("outline color=primary")
    return row
