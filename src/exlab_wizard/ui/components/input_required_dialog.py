"""Plugin ``INPUT_REQUIRED`` escalation dialog (Frontend Spec §9.1).

When a plugin suspends mid-creation to ask the operator for more input,
the controller publishes an ``input_required`` frame carrying the
plugin identity, a ``reason`` line, and a list of README-style field
declarations. This dialog renders those fields, collects the answers,
and hands them back so the caller can ``controller.resume(...)``.

Escalations are strictly sequential (§9.2): the dialog is ``persistent``
so it must be resolved (Submit / Cancel) before anything else happens.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from exlab_wizard.logging import get_logger

_log = get_logger(__name__)


def _field_id(field: dict[str, Any]) -> str | None:
    """Return the field's identifier (``id``, falling back to ``key``)."""
    fid = field.get("id") or field.get("key")
    return fid if isinstance(fid, str) and fid else None


def collect_default_values(fields: list[dict[str, Any]]) -> dict[str, Any]:
    """Seed an answers dict from each field's declared default.

    Booleans default to ``False`` when undeclared; every other type
    defaults to the empty string so :func:`input_required_dialog` always
    has a value to two-way bind against.
    """
    values: dict[str, Any] = {}
    for field in fields:
        fid = _field_id(field)
        if fid is None:
            continue
        ftype = str(field.get("type", "string"))
        default = field.get("default")
        if ftype == "boolean":
            values[fid] = bool(default) if default is not None else False
        else:
            values[fid] = default if default is not None else ""
    return values


def input_required_dialog(
    *,
    plugin: str,
    reason: str,
    fields: list[dict[str, Any]],
    on_submit: Callable[[dict[str, Any]], None],
    on_cancel: Callable[[], None],
) -> Any:
    """Build the §9.1 "Additional input required" dialog.

    Renders one widget per field (string/text/choice/date/boolean), a
    plugin-identity pill, and the reason line. ``on_submit`` receives the
    collected answers dict; ``on_cancel`` fires when the operator backs
    out. Returns the NiceGUI dialog (or, in tests, a payload describing
    the resolved fields/defaults).
    """
    values = collect_default_values(fields)
    payload = {"plugin": plugin, "reason": reason, "fields": fields, "values": values}

    try:
        from nicegui import ui
    except Exception:
        return payload

    # §9.2: persistent so the escalation must be resolved before the next
    # frame -- no click-away dismissal that would strand the pipeline.
    dialog = ui.dialog().props("persistent")

    def _submit() -> None:
        dialog.close()
        on_submit(dict(values))

    def _cancel() -> None:
        dialog.close()
        on_cancel()

    with (
        dialog,
        ui.card().props('data-testid="input-required-dialog"').style("min-width: 480px;"),
    ):
        ui.label("Additional input required").style(
            "font-family: var(--font-display); font-size: var(--text-md); "
            "color: var(--color-heading); font-weight: 600;"
        )
        ui.label(plugin).props('data-testid="input-required-plugin"').style(
            "font-family: var(--font-mono); font-size: var(--text-xs); "
            "color: var(--color-heading); background: var(--color-rule); "
            "padding: 0.1rem 0.5rem; border-radius: var(--radius-sm); align-self: flex-start;"
        )
        if reason:
            ui.label(reason).style("color: var(--color-body);")
        for field in fields:
            fid = _field_id(field)
            if fid is None:
                continue
            label = str(field.get("label", fid))
            ftype = str(field.get("type", "string"))
            if ftype == "boolean":
                ui.checkbox(label, value=values[fid]).bind_value(values, fid)
            elif ftype == "choice":
                options = [str(opt) for opt in (field.get("options") or [])]
                ui.select(options, label=label, value=values[fid] or None).bind_value(
                    values, fid
                ).classes("w-full")
            elif ftype == "text":
                ui.textarea(label, value=values[fid]).bind_value(values, fid).classes("w-full")
            else:  # string / date / anything else -> single-line input
                ui.input(label, value=values[fid]).bind_value(values, fid).classes("w-full")
        with ui.row().classes("justify-end w-full").style("gap: 0.5rem;"):
            ui.button("Cancel", on_click=lambda _e: _cancel()).props(
                'flat data-testid="input-required-cancel"'
            )
            ui.button("Submit", on_click=lambda _e: _submit()).props(
                'color=primary data-testid="input-required-submit"'
            )
    return dialog
