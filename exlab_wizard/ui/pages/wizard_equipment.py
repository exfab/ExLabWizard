"""Add-Equipment wizard (GUI/Orchestrator Redesign §6).

Five-step wizard launched from the main-window toolbar:

1. Identity — equipment ID (validated against ``^[A-Z][A-Z0-9_]*$``) +
   label.
2. Paths — local_root (where this device acquires runs).
3. Sync mode — pick ``nas`` (acquire + sync directly to NAS) or
   ``stage`` (acquire + push to a connected PC's staging area). The
   step then shows the matching transport sub-form.
4. Completeness signal — sentinel vs manifest + filename.
5. Review & confirm — assembles a validated EquipmentConfig via the
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
    "signal",
    "review",
)

EQUIPMENT_STEP_TITLES: dict[str, str] = {
    "identity": "Identity",
    "paths": "Paths",
    "sync_mode": "Sync mode",
    "signal": "Completeness signal",
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
    completeness_signal: str = "sentinel_file"
    sentinel_filename: str = ""
    manifest_filename: str = ""
    # Step 5
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
                    return bool(
                        state.rclone_remote.strip()
                        and state.rclone_remote_path.strip()
                    )
                return bool(
                    state.ssh_target.strip() and state.rsync_remote_path.strip()
                )
            # stage
            return bool(
                state.staging_mount_point.strip()
                and state.staging_subpath.strip()
            )
        case "signal":
            if state.completeness_signal == "sentinel_file":
                return bool(state.sentinel_filename.strip())
            return bool(state.manifest_filename.strip())
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
        completeness_signal=state.completeness_signal,
        sentinel_filename=state.sentinel_filename,
        manifest_filename=state.manifest_filename,
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
    on_advance: Callable[[str], None] | None = None,
    on_back: Callable[[str], None] | None = None,
    on_confirm: Callable[[EquipmentConfig], None] | None = None,
    on_cancel: Callable[[], None] | None = None,
) -> Any:
    """Render the Add-Equipment wizard. Pure render function."""
    s = state or EquipmentWizardState()

    try:
        from nicegui import ui
    except Exception:
        return {"state": s}

    with ui.dialog().props("persistent maximized").classes("w-full h-full") as dialog:
        with ui.card().classes("w-full h-full p-6"):
            ui.label("Add Equipment").style(
                "font-family: var(--font-display); font-size: var(--text-lg); "
                "color: var(--color-heading); font-weight: 600;"
            )
            ui.label(EQUIPMENT_STEP_TITLES[s.active_step]).style(
                "color: var(--color-muted); margin-bottom: var(--sp-3);"
            ).props(f'data-testid="wizard-equipment-step-{s.active_step}"')

            _STEP_RENDERERS[s.active_step](s)

            with ui.row().classes("items-center w-full").style(
                "margin-top: var(--sp-4); gap: var(--sp-2);"
            ):
                if on_cancel is not None:
                    ui.button("Cancel").props(
                        'flat data-testid="wizard-equipment-cancel"'
                    ).on("click", lambda _evt: on_cancel())
                if s.active_step != EQUIPMENT_WIZARD_STEPS[0] and on_back is not None:
                    ui.button("Back").props(
                        'flat data-testid="wizard-equipment-back"'
                    ).on("click", lambda _evt: on_back(s.active_step))
                ui.space()
                if s.active_step == "review":
                    ui.button("Confirm").props(
                        'color=primary data-testid="wizard-equipment-confirm"'
                    ).on(
                        "click",
                        lambda _evt: _maybe_confirm(s, on_confirm),
                    )
                else:
                    btn = ui.button("Next").props(
                        'color=primary data-testid="wizard-equipment-next"'
                    )
                    if on_advance is not None:
                        btn.on("click", lambda _evt: on_advance(s.active_step))
                    if not can_advance(s):
                        btn.props("disable")
    return dialog


def _maybe_confirm(
    state: EquipmentWizardState,
    on_confirm: Callable[[EquipmentConfig], None] | None,
) -> None:
    if on_confirm is None:
        return
    try:
        eq = assemble_equipment_config(state)
    except Exception as exc:
        state.last_error = str(exc)
        return
    on_confirm(eq)
    state.confirmed = True


def _render_identity_step(state: EquipmentWizardState) -> None:
    try:
        from nicegui import ui
    except Exception:
        return
    ui.input(label="Equipment ID (^[A-Z][A-Z0-9_]*$)").props(
        'data-testid="wizard-equipment-id"'
    ).bind_value(state, "equipment_id")
    ui.input(label="Label").props('data-testid="wizard-equipment-label"').bind_value(
        state, "label"
    )


def _render_paths_step(state: EquipmentWizardState) -> None:
    try:
        from nicegui import ui
    except Exception:
        return
    ui.input(label="Local root").props(
        'data-testid="wizard-equipment-local-root"'
    ).bind_value(state, "local_root")
    ui.input(label="NAS root").props(
        'data-testid="wizard-equipment-nas-root"'
    ).bind_value(state, "nas_root")


def _render_sync_mode_step(state: EquipmentWizardState) -> None:
    try:
        from nicegui import ui
    except Exception:
        return
    with ui.row().classes("items-center"):
        ui.radio(["nas", "stage"], value=state.sync_mode).props(
            'data-testid="wizard-equipment-sync-mode"'
        ).bind_value(state, "sync_mode")
    if state.sync_mode == "nas":
        ui.radio(["rclone", "rsync_ssh"], value=state.transport_type).props(
            'data-testid="wizard-equipment-transport-type"'
        ).bind_value(state, "transport_type")
        if state.transport_type == "rclone":
            ui.input(label="rclone remote").props(
                'data-testid="wizard-equipment-rclone-remote"'
            ).bind_value(state, "rclone_remote")
            ui.input(label="rclone remote path").props(
                'data-testid="wizard-equipment-rclone-remote-path"'
            ).bind_value(state, "rclone_remote_path")
        else:
            ui.input(label="SSH target").props(
                'data-testid="wizard-equipment-ssh-target"'
            ).bind_value(state, "ssh_target")
            ui.input(label="SSH key path").props(
                'data-testid="wizard-equipment-ssh-key-path"'
            ).bind_value(state, "ssh_key_path")
            ui.input(label="rsync remote path").props(
                'data-testid="wizard-equipment-rsync-remote-path"'
            ).bind_value(state, "rsync_remote_path")
    else:  # stage
        ui.radio(
            ["smb_mount", "file_transfer"], value=state.staging_transport_type
        ).props(
            'data-testid="wizard-equipment-staging-transport-type"'
        ).bind_value(state, "staging_transport_type")
        ui.input(label="Mount point").props(
            'data-testid="wizard-equipment-staging-mount-point"'
        ).bind_value(state, "staging_mount_point")
        ui.input(label="Staging subpath").props(
            'data-testid="wizard-equipment-staging-subpath"'
        ).bind_value(state, "staging_subpath")


def _render_signal_step(state: EquipmentWizardState) -> None:
    try:
        from nicegui import ui
    except Exception:
        return
    ui.radio(["sentinel_file", "manifest"], value=state.completeness_signal).props(
        'data-testid="wizard-equipment-signal"'
    ).bind_value(state, "completeness_signal")
    if state.completeness_signal == "sentinel_file":
        ui.input(label="Sentinel filename").props(
            'data-testid="wizard-equipment-sentinel-filename"'
        ).bind_value(state, "sentinel_filename")
    else:
        ui.input(label="Manifest filename").props(
            'data-testid="wizard-equipment-manifest-filename"'
        ).bind_value(state, "manifest_filename")


def _render_review_step(state: EquipmentWizardState) -> None:
    try:
        from nicegui import ui
    except Exception:
        return
    ui.label("Review your equipment configuration:").style(
        "color: var(--color-muted);"
    )
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
        ui.label(
            f"Completeness signal: {state.completeness_signal} / "
            + (state.sentinel_filename or state.manifest_filename)
        )
    if state.last_error:
        ui.label(f"Error: {state.last_error}").style(
            "color: var(--color-danger);"
        ).props('data-testid="wizard-equipment-error"')


_STEP_RENDERERS: dict[str, Callable[[EquipmentWizardState], None]] = {
    "identity": _render_identity_step,
    "paths": _render_paths_step,
    "sync_mode": _render_sync_mode_step,
    "signal": _render_signal_step,
    "review": _render_review_step,
}
