"""Add-Equipment wizard (GUI/Orchestrator Redesign §6).

Four-step wizard launched from the main-window toolbar:

1. Identity — equipment ID (validated against ``^[A-Z][A-Z0-9_]*$``) +
   label.
2. Paths — local_root (where this device acquires runs).
3. Sync mode — pick ``nas`` (acquire + sync directly to NAS) or
   ``stage`` (acquire + push to a connected PC's staging area). The
   step then shows the matching transport sub-form.
4. Review & confirm — assembles a validated EquipmentConfig via the
   shared ``build_equipment_config()`` and posts it through
   ``POST /config/equipment``.

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


EQUIPMENT_WIZARD_STEPS: tuple[str, ...] = (
    "identity",
    "paths",
    "sync_mode",
    "review",
)

EQUIPMENT_STEP_TITLES: dict[str, str] = {
    "identity": "Identity",
    "paths": "Paths",
    "sync_mode": "Sync mode",
    "review": "Review & confirm",
}


@dataclass
class EquipmentWizardState:
    """Mutable state for the in-flight Add-Equipment wizard."""

    active_step: str = EQUIPMENT_WIZARD_STEPS[0]
    # Step 1
    equipment_id: str = ""
    label: str = ""
    # Step 2
    local_root: str = ""
    nas_root: str = ""
    # Step 3
    sync_mode: str = "nas"
    transport_type: str = "rclone"
    rclone_remote: str = ""
    rclone_remote_path: str = ""
    ssh_target: str = ""
    ssh_key_path: str = ""
    rsync_remote_path: str = ""
    staging_transport_type: str = "smb_mount"
    staging_mount_point: str = ""
    staging_subpath: str = ""
    # Step 4
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
            return bool(state.local_root.strip() and state.nas_root.strip())
        case "sync_mode":
            if state.sync_mode == "nas":
                if state.transport_type == "rclone":
                    return bool(state.rclone_remote.strip() and state.rclone_remote_path.strip())
                return bool(state.ssh_target.strip() and state.rsync_remote_path.strip())
            # stage
            return bool(state.staging_mount_point.strip() and state.staging_subpath.strip())
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
        local_root=state.local_root,
        nas_root=state.nas_root,
        sync_mode=state.sync_mode,
        transport_type=state.transport_type,
        rclone_remote=state.rclone_remote,
        rclone_remote_path=state.rclone_remote_path,
        ssh_target=state.ssh_target,
        ssh_key_path=state.ssh_key_path,
        rsync_remote_path=state.rsync_remote_path,
        staging_transport_type=state.staging_transport_type,
        staging_mount_point=state.staging_mount_point,
        staging_subpath=state.staging_subpath,
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
    ui.input(label="Local root", on_change=lambda _e: sync_next()).props(
        'data-testid="wizard-equipment-local-root"'
    ).bind_value(state, "local_root")
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
    with ui.row().classes("items-center"):
        ui.radio(
            ["nas", "stage"], value=state.sync_mode, on_change=lambda _e: refresh_body()
        ).props('data-testid="wizard-equipment-sync-mode"').bind_value(state, "sync_mode")
    if state.sync_mode == "nas":
        ui.radio(
            ["rclone", "rsync_ssh"],
            value=state.transport_type,
            on_change=lambda _e: refresh_body(),
        ).props('data-testid="wizard-equipment-transport-type"').bind_value(state, "transport_type")
        if state.transport_type == "rclone":
            ui.input(label="rclone remote", on_change=lambda _e: sync_next()).props(
                'data-testid="wizard-equipment-rclone-remote"'
            ).bind_value(state, "rclone_remote")
            ui.input(label="rclone remote path", on_change=lambda _e: sync_next()).props(
                'data-testid="wizard-equipment-rclone-remote-path"'
            ).bind_value(state, "rclone_remote_path")
        else:
            ui.input(label="SSH target", on_change=lambda _e: sync_next()).props(
                'data-testid="wizard-equipment-ssh-target"'
            ).bind_value(state, "ssh_target")
            ui.input(label="SSH key path", on_change=lambda _e: sync_next()).props(
                'data-testid="wizard-equipment-ssh-key-path"'
            ).bind_value(state, "ssh_key_path")
            ui.input(label="rsync remote path", on_change=lambda _e: sync_next()).props(
                'data-testid="wizard-equipment-rsync-remote-path"'
            ).bind_value(state, "rsync_remote_path")
    else:  # stage
        ui.radio(
            ["smb_mount", "file_transfer"],
            value=state.staging_transport_type,
            on_change=lambda _e: refresh_body(),
        ).props('data-testid="wizard-equipment-staging-transport-type"').bind_value(
            state, "staging_transport_type"
        )
        ui.input(label="Mount point", on_change=lambda _e: sync_next()).props(
            'data-testid="wizard-equipment-staging-mount-point"'
        ).bind_value(state, "staging_mount_point")
        ui.input(label="Staging subpath", on_change=lambda _e: sync_next()).props(
            'data-testid="wizard-equipment-staging-subpath"'
        ).bind_value(state, "staging_subpath")


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
        ui.label(f"Local root: {state.local_root}")
        ui.label(f"NAS root: {state.nas_root}")
        ui.label(f"Sync mode: {state.sync_mode}")
        if state.sync_mode == "nas":
            ui.label(f"Transport: {state.transport_type}")
        else:
            ui.label(f"Staging transport: {state.staging_transport_type}")
            ui.label(f"Mount point: {state.staging_mount_point}")
            ui.label(f"Staging subpath: {state.staging_subpath}")
    if state.last_error:
        ui.label(f"Error: {state.last_error}").style("color: var(--color-danger);").props(
            'data-testid="wizard-equipment-error"'
        )


_StepRenderer = Callable[[EquipmentWizardState, Callable[[], object], Callable[[], object]], None]

_STEP_RENDERERS: dict[str, _StepRenderer] = {
    "identity": _render_identity_step,
    "paths": _render_paths_step,
    "sync_mode": _render_sync_mode_step,
    "review": _render_review_step,
}
