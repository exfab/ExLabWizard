"""Credential field component (Frontend Spec §7.4.1).

Three resting / transient states:

* **not_set**  -- ``Status: Not set``  with a ``[Set]`` button.
* **set**      -- ``Status: Set ✓``    with ``[Replace]`` and ``[Clear]``.
* **editing**  -- inline password input with Save / Cancel.

Never displays a stored secret.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from exlab_wizard.logging import get_logger

_log = get_logger(__name__)

STATE_NOT_SET = "not_set"
STATE_SET = "set"
STATE_EDITING = "editing"


@dataclass
class CredentialState:
    """In-memory state for a credential row."""

    state: str = STATE_NOT_SET
    pending_value: str | None = None  # only populated while editing


def credential_props(state: CredentialState) -> dict[str, object]:
    """Compute the visible labels / button names for a state.

    Returns a dict with ``state``, ``label``, ``primary_button``,
    ``secondary_button`` (optional), and ``input_visible`` keys.
    """

    if state.state == STATE_NOT_SET:
        return {
            "state": STATE_NOT_SET,
            "label": "Status: Not set",
            "primary_button": "Set",
            "secondary_button": None,
            "input_visible": False,
        }
    if state.state == STATE_SET:
        return {
            "state": STATE_SET,
            "label": "Status: Set",
            "primary_button": "Replace",
            "secondary_button": "Clear",
            "input_visible": False,
        }
    return {
        "state": STATE_EDITING,
        "label": "Status: Editing",
        "primary_button": "Save",
        "secondary_button": "Cancel",
        "input_visible": True,
    }


def credential_field(
    *,
    label: str,
    on_save: Callable[[str], None],
    on_clear: Callable[[], None],
    initial_state: CredentialState | None = None,
) -> Any:
    """Build a credential row.

    The ``on_save`` callback is invoked with the typed password when the
    operator clicks Save while editing; ``on_clear`` is invoked when the
    operator confirms the Clear action.
    """

    state = initial_state or CredentialState()
    props = credential_props(state)
    payload = {"label": label, "props": props}
    try:
        from nicegui import ui
    except Exception:
        return payload

    container = ui.column().classes("w-full").style("gap: 0.25rem;")
    with container:
        ui.label(label).style(
            "font-family: var(--font-mono); "
            "font-size: var(--text-xs); "
            "letter-spacing: 0.08em; "
            "text-transform: uppercase; "
            "color: var(--color-muted);"
        )
        with ui.row().classes("items-center"):
            status_label = ui.label(props["label"]).style(
                "font-family: var(--font-mono); font-size: var(--text-sm);"
            )

            def _set_state(new_state: str) -> None:
                state.state = new_state
                state.pending_value = None
                container.clear()
                with container:
                    ui.label(label)
                    new_props = credential_props(state)
                    status_label.text = new_props["label"]

            def _on_primary() -> None:
                if state.state == STATE_NOT_SET or state.state == STATE_SET:
                    state.state = STATE_EDITING
                else:  # editing
                    if state.pending_value:
                        on_save(state.pending_value)
                        state.state = STATE_SET
                _set_state(state.state)

            def _on_secondary() -> None:
                if state.state == STATE_EDITING:
                    state.state = STATE_NOT_SET
                elif state.state == STATE_SET:
                    on_clear()
                    state.state = STATE_NOT_SET
                _set_state(state.state)

            ui.button(props["primary_button"], on_click=_on_primary).props("flat")
            if props["secondary_button"]:
                ui.button(props["secondary_button"], on_click=_on_secondary).props("flat")

    return container
