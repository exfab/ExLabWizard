"""Add-Equipment wizard (GUI/Orchestrator Redesign §6).

Three-step wizard launched from the main-window toolbar:

1. Identity — equipment ID (validated against ``^[A-Z][A-Z0-9_]*$``) +
   label.
2. Paths — NAS root (the equipment's data dir is derived from the single
   app root, so it is not collected here).
3. Review & confirm — assembles a validated EquipmentConfig via the
   shared ``build_equipment_config()`` and posts it through
   ``POST /config/equipment``.

The sync-mode step is intentionally hidden: orchestrator / staging is
hidden at the UI layer (see
``docs/superpowers/specs/2026-05-29-hide-orchestrator-staging-design.md``),
so every equipment is created in ``nas`` mode (sync directly to NAS via
the single ``nas:`` remote). The dormant ``_render_sync_mode_step`` is
kept so re-listing it restores the step verbatim.

The render function is pure (state + callbacks); the actual NiceGUI
mount layer wires the on-confirm callback to the config router.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from exlab_wizard.config.models import EquipmentConfig
from exlab_wizard.constants.patterns import EQUIPMENT_ID_PATTERN
from exlab_wizard.logging import get_logger
from exlab_wizard.ui.equipment_form import build_equipment_config

_log = get_logger(__name__)


# The "sync_mode" step is intentionally omitted: orchestrator / staging is
# hidden at the UI layer (see
# docs/superpowers/specs/2026-05-29-hide-orchestrator-staging-design.md).
# Every equipment is created in ``nas`` mode. Re-listing "sync_mode" here and
# in ``EQUIPMENT_STEP_TITLES`` / ``_STEP_RENDERERS`` below restores the step
# verbatim (the renderer is kept, dormant).
EQUIPMENT_WIZARD_STEPS: tuple[str, ...] = (
    "identity",
    "paths",
    "review",
)

EQUIPMENT_STEP_TITLES: dict[str, str] = {
    "identity": "Identity",
    "paths": "Paths",
    "review": "Review & confirm",
}


@dataclass
class EquipmentWizardState:
    """Mutable state for the in-flight Add-Equipment wizard."""

    active_step: str = EQUIPMENT_WIZARD_STEPS[0]
    # Step 1
    equipment_id: str = ""
    label: str = ""
    # Step 2 -- the equipment's data dir is derived from the single app root
    # (``<config.paths.local_root>/<id>``), so only the NAS root is collected.
    nas_root: str = ""
    # sync_mode is retained but no longer operator-selectable: the sync-mode
    # wizard step is hidden (orchestrator/staging hidden — see module note),
    # so every equipment is created in "nas" mode. The field stays so the
    # dormant ``_render_sync_mode_step`` and ``SyncMode.STAGE`` backend remain
    # one edit away from re-enabling.
    sync_mode: str = "nas"
    # Review step
    last_error: str | None = None
    confirmed: bool = False


def can_advance(state: EquipmentWizardState) -> bool:
    """Return True if the active step has the data it needs to advance.

    Pure function — surfaces the per-step gate so unit tests can assert
    the wizard's progression without rendering NiceGUI.
    """
    match state.active_step:
        case "identity":
            return bool(
                state.equipment_id
                and EQUIPMENT_ID_PATTERN.fullmatch(state.equipment_id)
                and state.label.strip()
            )
        case "paths":
            return bool(state.nas_root.strip())
        case "sync_mode":
            # rclone.conf migration (Phase 8): neither mode collects a
            # per-equipment transport here -- the ``nas:`` remote defines the
            # NAS connection and ``orchestrator.staging_remote`` defines the
            # staging hop -- so picking the mode is enough to advance.
            return True
        case "review":
            return True
    return False


def assemble_equipment_config(
    state: EquipmentWizardState,
) -> EquipmentConfig:
    """Build the final EquipmentConfig from the wizard's state.

    Raises a pydantic ValidationError if the state isn't valid; the
    caller surfaces that to the operator.
    """
    return build_equipment_config(
        equipment_id=state.equipment_id,
        label=state.label,
        nas_root=state.nas_root,
        sync_mode=state.sync_mode,
    )


def render_wizard_equipment(
    *,
    state: EquipmentWizardState | None = None,
    on_confirm: Callable[[EquipmentConfig], None] | None = None,
    on_cancel: Callable[[], None] | None = None,
) -> Any:  # pragma: no cover -- NiceGUI render, driven by e2e
    """Render the self-contained Add-Equipment wizard.

    Next / Back navigation is handled *inside* the render: the step body
    and footer live in one ``@ui.refreshable`` so advancing mutates
    ``state.active_step`` and re-renders in place. The wizard therefore
    keeps a single ``EquipmentWizardState`` for its whole lifetime --
    the caller creates it once and never round-trips through a page
    navigation that would reset it. Only ``on_confirm`` (post the
    assembled ``EquipmentConfig``) and ``on_cancel`` (leave the wizard)
    cross back to the host.

    Rendered as a full-page card since the wizard owns the
    ``/wizard/equipment`` route.
    """
    s = state or EquipmentWizardState()

    try:
        from nicegui import ui
    except Exception:
        return {"state": s}

    # Handle to the live Next button. ``_body`` rewrites this on every
    # re-render; ``_sync_next`` toggles the button's enabled state as the
    # operator edits a step -- without a re-render, so input focus is
    # kept while typing.
    next_btn: dict[str, Any] = {}

    with ui.card().classes("w-full h-full p-6").props('data-testid="wizard-equipment"') as dialog:
        ui.label("Add Equipment").style(
            "font-family: var(--font-display); font-size: var(--text-lg); "
            "color: var(--color-heading); font-weight: 600;"
        )

        def _sync_next() -> None:
            """Re-evaluate ``can_advance`` and enable/disable Next in place."""
            btn = next_btn.get("btn")
            if btn is not None:
                btn.set_enabled(can_advance(s))

        def _step_forward() -> None:
            """Advance one step, but only when the current step is valid."""
            if not can_advance(s):
                return
            idx = EQUIPMENT_WIZARD_STEPS.index(s.active_step)
            if idx + 1 < len(EQUIPMENT_WIZARD_STEPS):
                s.active_step = EQUIPMENT_WIZARD_STEPS[idx + 1]
                _body.refresh()

        def _step_back() -> None:
            """Return to the previous step, keeping every entered value."""
            idx = EQUIPMENT_WIZARD_STEPS.index(s.active_step)
            if idx > 0:
                s.active_step = EQUIPMENT_WIZARD_STEPS[idx - 1]
                _body.refresh()

        @ui.refreshable
        def _body() -> None:
            next_btn.pop("btn", None)
            ui.label(EQUIPMENT_STEP_TITLES[s.active_step]).style(
                "color: var(--color-muted); margin-bottom: var(--sp-3);"
            ).props(f'data-testid="wizard-equipment-step-{s.active_step}"')

            # Step renderers wire radios to ``_body.refresh`` (a changed
            # radio swaps which sub-form is shown) and text inputs to
            # ``_sync_next`` (re-checks the Next gate without a re-render).
            _STEP_RENDERERS[s.active_step](s, _body.refresh, _sync_next)

            with (
                ui.row()
                .classes("items-center w-full")
                .style("margin-top: var(--sp-4); gap: var(--sp-2);")
            ):
                if on_cancel is not None:
                    cancel_cb = on_cancel
                    ui.button("Cancel").props('flat data-testid="wizard-equipment-cancel"').on(
                        "click", lambda _evt: cancel_cb()
                    )
                if s.active_step != EQUIPMENT_WIZARD_STEPS[0]:
                    ui.button("Back").props('flat data-testid="wizard-equipment-back"').on(
                        "click", lambda _evt: _step_back()
                    )
                ui.space()
                if s.active_step == "review":
                    ui.button("Confirm").props(
                        'color=primary data-testid="wizard-equipment-confirm"'
                    ).on("click", lambda _evt: _maybe_confirm(s, on_confirm))
                else:
                    btn = ui.button("Next").props(
                        'color=primary data-testid="wizard-equipment-next"'
                    )
                    btn.on("click", lambda _evt: _step_forward())
                    btn.set_enabled(can_advance(s))
                    next_btn["btn"] = btn

        _body()
    return dialog


def _maybe_confirm(
    state: EquipmentWizardState,
    on_confirm: Callable[[EquipmentConfig], None] | None,
) -> None:  # pragma: no cover -- NiceGUI render, driven by e2e
    if on_confirm is None:
        return
    try:
        eq = assemble_equipment_config(state)
    except Exception as exc:
        state.last_error = str(exc)
        return
    on_confirm(eq)
    state.confirmed = True


def _render_identity_step(
    state: EquipmentWizardState,
    refresh_body: Callable[[], object],
    sync_next: Callable[[], object],
) -> None:  # pragma: no cover -- NiceGUI render, driven by e2e
    del refresh_body  # identity has no structural (radio) controls
    try:
        from nicegui import ui
    except Exception:
        return
    ui.input(label="Equipment ID (^[A-Z][A-Z0-9_]*$)", on_change=lambda _e: sync_next()).props(
        'data-testid="wizard-equipment-id"'
    ).bind_value(state, "equipment_id")
    ui.input(label="Label", on_change=lambda _e: sync_next()).props(
        'data-testid="wizard-equipment-label"'
    ).bind_value(state, "label")


def _render_paths_step(
    state: EquipmentWizardState,
    refresh_body: Callable[[], object],
    sync_next: Callable[[], object],
) -> None:  # pragma: no cover -- NiceGUI render, driven by e2e
    del refresh_body  # paths has no structural (radio) controls
    try:
        from nicegui import ui
    except Exception:
        return
    # The equipment's data dir is derived from the single app root
    # (Settings -> Data folder); only the NAS root is collected here.
    ui.input(label="NAS root", on_change=lambda _e: sync_next()).props(
        'data-testid="wizard-equipment-nas-root"'
    ).bind_value(state, "nas_root")


def _render_sync_mode_step(
    state: EquipmentWizardState,
    refresh_body: Callable[[], object],
    sync_next: Callable[[], object],
) -> None:  # pragma: no cover -- NiceGUI render, driven by e2e
    try:
        from nicegui import ui
    except Exception:
        return
    del sync_next  # the sync-mode step has only the radio (no text inputs)
    with ui.row().classes("items-center"):
        ui.radio(
            ["nas", "stage"], value=state.sync_mode, on_change=lambda _e: refresh_body()
        ).props('data-testid="wizard-equipment-sync-mode"').bind_value(state, "sync_mode")
    if state.sync_mode == "nas":
        # rclone.conf migration: nas-mode no longer collects a per-equipment
        # SFTP/SMB transport here. The connection is defined once by the
        # single ``nas:`` remote in the operator's rclone.conf (confirmed
        # via Settings -> NAS Remote -> Test connection).
        ui.label(
            "This device syncs directly to the NAS using the rclone remote "
            "configured in Settings -> NAS Remote. No per-equipment connection "
            "is needed here."
        ).style("color: var(--color-muted); font-size: var(--text-sm);").props(
            'data-testid="wizard-equipment-nas-note"'
        )
    else:  # stage
        # rclone.conf migration (Phase 8): stage-mode no longer collects a
        # per-equipment mount/subpath here. The staging hop is defined once
        # by ``orchestrator.staging_remote`` / ``staging_base_root`` (a
        # second rclone remote configured in Settings).
        ui.label(
            "This device pushes runs to the staging-PC rclone remote "
            "configured under orchestrator.staging_remote. No per-equipment "
            "connection is needed here."
        ).style("color: var(--color-muted); font-size: var(--text-sm);").props(
            'data-testid="wizard-equipment-stage-note"'
        )


def _render_review_step(
    state: EquipmentWizardState,
    refresh_body: Callable[[], object],
    sync_next: Callable[[], object],
) -> None:  # pragma: no cover -- NiceGUI render, driven by e2e
    del refresh_body, sync_next  # review is a static summary, no inputs
    try:
        from nicegui import ui
    except Exception:
        return
    ui.label("Review your equipment configuration:").style("color: var(--color-muted);")
    with ui.column().style("font-family: var(--font-mono);"):
        ui.label(f"ID: {state.equipment_id}")
        ui.label(f"Label: {state.label}")
        ui.label(f"NAS root: {state.nas_root}")
    if state.sync_mode == "nas":
        ui.label(
            "This device syncs directly to the NAS via the rclone remote "
            "configured in Settings → NAS Remote."
        ).style(
            "color: var(--color-muted); margin-top: var(--sp-2); "
            "font-size: var(--text-xs); font-style: italic;"
        ).props('data-testid="wizard-equipment-credential-hint"')
    if state.last_error:
        ui.label(f"Error: {state.last_error}").style("color: var(--color-danger);").props(
            'data-testid="wizard-equipment-error"'
        )


_StepRenderer = Callable[[EquipmentWizardState, Callable[[], object], Callable[[], object]], None]

_STEP_RENDERERS: dict[str, _StepRenderer] = {
    "identity": _render_identity_step,
    "paths": _render_paths_step,
    # "sync_mode": _render_sync_mode_step,  # hidden — see EQUIPMENT_WIZARD_STEPS note
    "review": _render_review_step,
}
