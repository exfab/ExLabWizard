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
    """In-memory state for a credential row.

    ``resting`` records which resting state (``not_set`` / ``set``) the
    row should collapse back to when the operator cancels an edit -- a
    fresh *Set* cancels back to ``not_set``, a *Replace* cancels back to
    ``set``.
    """

    state: str = STATE_NOT_SET
    resting: str = STATE_NOT_SET


def begin_edit(state: CredentialState) -> CredentialState:
    """Return the editing state, remembering ``state`` as the resting target.

    Idempotent: calling it on an already-editing row leaves ``resting``
    untouched so a stray double-click does not lose the original
    resting state.
    """

    if state.state == STATE_EDITING:
        return CredentialState(state=STATE_EDITING, resting=state.resting)
    return CredentialState(state=STATE_EDITING, resting=state.state)


def cancel_edit(state: CredentialState) -> CredentialState:
    """Return the resting state to collapse to when an edit is cancelled."""

    return CredentialState(state=state.resting)


def commit_edit(state: CredentialState, typed_value: str) -> tuple[CredentialState, bool]:
    """Resolve a Save click.

    Returns ``(new_state, should_save)``. A non-empty ``typed_value``
    transitions the row to ``set`` and signals the caller to invoke its
    ``on_save`` callback; an empty value has nothing to persist, so the
    row stays in the editor and ``should_save`` is ``False``.
    """

    if not typed_value:
        return CredentialState(state=STATE_EDITING, resting=state.resting), False
    return CredentialState(state=STATE_SET), True


def clear_credential(state: CredentialState) -> CredentialState:
    """Return the state after the operator confirms a Clear action."""

    del state  # the row always collapses to not_set regardless of prior state
    return CredentialState(state=STATE_NOT_SET)


def credential_props(state: CredentialState) -> dict[str, Any]:
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
    data_testid: str | None = None,
) -> Any:
    """Build a credential row (Frontend Spec §7.4.1).

    Three states drive the row: ``not_set`` shows a ``[Set]`` button;
    ``set`` shows ``[Replace]`` + ``[Clear]``; ``editing`` reveals an
    inline password input with Save / Cancel. ``on_save`` is invoked
    with the typed password when the operator clicks Save with a
    non-empty value; ``on_clear`` is invoked when the operator confirms
    the Clear action. The stored secret is never displayed.

    The row re-renders itself in place on every state transition via a
    NiceGUI ``@ui.refreshable`` body, so the input box, status line, and
    buttons always reflect the current state.

    ``data_testid``, when supplied, is the base for the row's e2e hooks:
    ``<base>-status`` / ``-primary`` / ``-secondary`` / ``-input`` /
    ``-save`` / ``-cancel`` / ``-clear-confirm``.
    """

    state = initial_state or CredentialState()
    payload = {"label": label, "props": credential_props(state)}
    try:
        from nicegui import ui
    except Exception:
        return payload

    def _tid(suffix: str) -> str:
        """Return the ``data-testid`` props fragment for a sub-element."""

        return f'data-testid="{data_testid}-{suffix}"' if data_testid else ""

    container = ui.column().classes("w-full").style("gap: 0.25rem;")
    with container:
        ui.label(label).style(
            "font-family: var(--font-mono); "
            "font-size: var(--text-xs); "
            "letter-spacing: 0.08em; "
            "text-transform: uppercase; "
            "color: var(--color-muted);"
        )

        def _apply(new_state: CredentialState) -> None:
            """Adopt ``new_state`` onto the live state and re-render the row."""

            state.state = new_state.state
            state.resting = new_state.resting
            _row.refresh()

        def _confirm_clear() -> None:
            """Open the Clear confirmation dialog (Frontend Spec §7.4.1)."""

            with ui.dialog() as dialog, ui.card():
                ui.label(
                    "Remove the stored password? You will be prompted to "
                    "re-enter it on the next API call."
                )
                with ui.row().classes("justify-end w-full"):
                    ui.button("Cancel", on_click=dialog.close).props("flat")

                    def _do_clear() -> None:
                        on_clear()
                        dialog.close()
                        _apply(clear_credential(state))

                    ui.button("Remove password", on_click=_do_clear).props(
                        f"color=negative {_tid('clear-confirm')}"
                    )
            dialog.open()

        @ui.refreshable
        def _row() -> None:
            props = credential_props(state)
            with ui.row().classes("items-center").style("gap: var(--sp-2);"):
                if props["input_visible"]:
                    password_input = ui.input(label="Password", password=True).props(_tid("input"))

                    def _save() -> None:
                        value = password_input.value or ""
                        new_state, should_save = commit_edit(state, value)
                        if should_save:
                            on_save(value)
                        _apply(new_state)

                    ui.button("Save", on_click=_save).props(f"flat {_tid('save')}")
                    ui.button("Cancel", on_click=lambda: _apply(cancel_edit(state))).props(
                        f"flat {_tid('cancel')}"
                    )
                else:
                    ui.label(props["label"]).props(_tid("status")).style(
                        "font-family: var(--font-mono); font-size: var(--text-sm);"
                    )
                    ui.button(
                        props["primary_button"],
                        on_click=lambda: _apply(begin_edit(state)),
                    ).props(f"flat {_tid('primary')}")
                    if props["secondary_button"]:
                        ui.button(props["secondary_button"], on_click=_confirm_clear).props(
                            f"flat {_tid('secondary')}"
                        )

        _row()

    return container
