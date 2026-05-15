"""Settings dialog (Frontend Spec §7).

Two-pane modal with a left vertical-nav and a right content area. Eight
sections (``operators`` is deferred pending the chip editor);
setup-incomplete mode auto-selects the first incomplete one.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from exlab_wizard.config.models import Config
from exlab_wizard.constants import CompletenessSignal
from exlab_wizard.logging import get_logger
from exlab_wizard.ui import notifications
from exlab_wizard.ui.components import credential_field, test_connection_panel

_log = get_logger(__name__)


SETTINGS_SECTIONS: tuple[str, ...] = (
    "paths",
    "lims",
    "equipment",
    "nas_cleanup",
    # "operators" is deferred -- backend OperatorsConfig + the
    # controller/creation.py allowlist gate stay wired and are no-ops while
    # the allowlist defaults to []. The chip editor lands in a future update.
    "validator",
    "logging",
    "orchestrator",
    "application",
)

SECTION_TITLES: dict[str, str] = {
    "paths": "Paths",
    "lims": "LIMS",
    "equipment": "Equipment List",
    "nas_cleanup": "NAS Cleanup",
    "validator": "Validator",
    "logging": "Logging",
    "orchestrator": "Orchestrator Mode",
    "application": "Application",
}


@dataclass
class SettingsState:
    """Mutable state for the dialog."""

    active_section: str = "paths"
    incomplete_sections: tuple[str, ...] = ()
    dirty_sections: set[str] = field(default_factory=set)
    pending_change_count: int = 0


def first_incomplete_section(incomplete: tuple[str, ...]) -> str | None:
    """Return the first section ID in canonical order that's incomplete."""

    for section in SETTINGS_SECTIONS:
        if section in incomplete:
            return section
    return None


def save_button_label(state: SettingsState) -> str:
    """Compute the *Save all* button label, including the badge count."""

    if state.incomplete_sections:
        return "Save and continue"
    if state.pending_change_count == 0:
        return "Save all changes"
    return f"Save all ({state.pending_change_count} changes)"


def section_has_warning(state: SettingsState, section: str) -> bool:
    """Return ``True`` when the sidebar should decorate ``section``."""

    return section in state.incomplete_sections


def section_is_dirty(state: SettingsState, section: str) -> bool:
    """Return ``True`` when ``section`` has uncommitted edits."""

    return section in state.dirty_sections


def build_settings_draft(config: Config | None) -> Config:
    """Return the editable deep-copy draft the settings dialog mutates.

    ``None`` (a fresh install with no ``config.yaml``) yields a
    ``Config()`` carrying the §9 defaults so every field still has a
    sensible starting value.
    """

    return (config or Config()).model_copy(deep=True)


def finalize_settings_draft(draft: Config) -> Config:
    """Re-validate a mutated draft into a clean :class:`Config`.

    The dialog's two-way bindings mutate the draft without running
    Pydantic validation (assignment validation is off on the model), so
    the Save handler round-trips ``model_dump`` -> ``model_validate`` to
    coerce widget types (e.g. ``ui.number`` floats back to ints) and
    enforce the §9 cross-field invariants. Raises ``ValidationError``
    when the edited values do not form a valid config.

    ``warnings=False`` on the dump silences Pydantic's "expected int,
    got float" notice -- the float is an artefact of ``ui.number`` and
    ``model_validate`` coerces it back to ``int`` on the next line.
    """

    return Config.model_validate(draft.model_dump(mode="python", warnings=False))


def lims_credential_initial_state(*, present: bool) -> credential_field.CredentialState:
    """Return the credential-row state seeding the LIMS password field.

    A password already in the OS keyring opens the row in the *Set*
    resting state (``[Replace]`` / ``[Clear]``); an empty keyring opens
    it in *Not set* (``[Set]``). ``resting`` matches ``state`` so a
    cancelled *Replace* collapses back to where it started.
    """

    name = credential_field.STATE_SET if present else credential_field.STATE_NOT_SET
    return credential_field.CredentialState(state=name, resting=name)


def render_settings_page(
    *,
    config: Config | None = None,
    state: SettingsState | None = None,
    on_save: Callable[[Config], None] | None = None,
    on_discard: Callable[[SettingsState], None] | None = None,
    on_select_section: Callable[[str], None] | None = None,
    on_save_lims_password: Callable[[str], None] | None = None,
    on_clear_lims_password: Callable[[], None] | None = None,
    lims_password_present: bool = False,
) -> Any:
    """Render the settings dialog.

    ``config`` is the live ``config.yaml`` model (or ``None`` on a fresh
    install). The dialog edits an in-memory deep copy -- the *draft* --
    so cancelling discards the edits; ``on_save`` receives the validated
    :class:`Config` built from the draft when the operator clicks Save.

    ``on_select_section`` is invoked when the operator clicks a sidebar
    nav row. The Phase 12 cut bound this to a no-op (the selection
    cycle is handled by the host page); the e2e harness wires it to a
    navigation hook so each section's body becomes assertable.

    ``on_save_lims_password`` / ``on_clear_lims_password`` back the LIMS
    section's credential field. Per Frontend Spec §7.3 credentials are
    independent of Save -- these write straight to the OS keyring at
    click time, so the host wires them to a :class:`KeyringStore` rather
    than to the draft. ``lims_password_present`` seeds the credential
    row's resting state from whether the keyring already holds one.
    """

    s = state or SettingsState()
    if s.incomplete_sections and s.active_section not in s.incomplete_sections:
        # Setup-incomplete mode: auto-select the first incomplete section
        # unless the caller has already pinned a specific section to render
        # (for example, after the operator clicks a sidebar nav row).
        first = first_incomplete_section(s.incomplete_sections)
        if first is not None:
            s = SettingsState(
                active_section=first,
                incomplete_sections=s.incomplete_sections,
                dirty_sections=s.dirty_sections,
                pending_change_count=s.pending_change_count,
            )

    # The dialog mutates this draft in place via two-way bindings; the
    # caller's ``config`` is never touched until ``on_save`` fires with
    # the re-validated result.
    draft = build_settings_draft(config)

    payload = {
        "active": s.active_section,
        "save_label": save_button_label(s),
        "warnings": [section for section in SETTINGS_SECTIONS if section_has_warning(s, section)],
        "config": draft.model_dump(mode="python"),
    }

    try:
        from nicegui import ui
    except Exception:
        return payload

    card = (
        ui.card()
        .props('data-testid="settings-dialog"')
        .style(
            "min-width: 880px; min-height: 600px; "
            "padding: var(--sp-4); "
            "background: var(--color-surface); "
            "border-radius: var(--radius-md); "
            "box-shadow: var(--shadow-md);"
        )
    )
    with card:
        if s.incomplete_sections:
            ui.label(
                "Setup incomplete. Configure the highlighted sections to start using ExLab-Wizard.",
            ).props('data-testid="settings-incomplete-banner"').style(
                "padding: 0.75rem 1rem; "
                "border-left: 4px solid var(--color-warning); "
                "background: rgba(230,159,0,0.07); "
                "border-radius: var(--radius);"
            )
        # Every section body is rendered up front, bound to the single
        # shared ``draft``, and shown/hidden client-side. A nav click
        # only toggles visibility -- it never reloads the page -- so
        # edits made in one section survive switching to another.
        section_bodies: dict[str, Any] = {}

        def _select_section(section: str) -> None:
            for name, body in section_bodies.items():
                body.visible = name == section
            if on_select_section is not None:
                on_select_section(section)

        with ui.splitter(value=22).classes("w-full") as split:
            with split.before, ui.column().classes("w-full").style("gap: 0.25rem;"):
                for section in SETTINGS_SECTIONS:
                    nav_row = (
                        ui.row()
                        .classes("items-center w-full")
                        .props(f'data-testid="settings-nav-{section}"')
                        .style(
                            "padding: 0.5rem 0.75rem; cursor: pointer;",
                        )
                    )
                    nav_row.on(
                        "click",
                        lambda _evt, sec=section: _select_section(sec),
                    )
                    with nav_row:
                        ui.label(SECTION_TITLES[section]).style(
                            "font-family: var(--font-body); "
                            "font-size: var(--text-sm);"
                            + (
                                " font-weight: 600; color: var(--color-heading);"
                                if section == s.active_section
                                else " color: var(--color-body);"
                            )
                        )
                        if section_is_dirty(s, section):
                            ui.label("•").style("color: var(--color-info);")
                        if section_has_warning(s, section):
                            ui.icon("warning").style("color: var(--color-warning);")
            with split.after:
                for section in SETTINGS_SECTIONS:
                    body = ui.column().classes("w-full")
                    body.visible = section == s.active_section
                    with body:
                        _render_section_body(
                            section,
                            draft,
                            on_save_lims_password=on_save_lims_password,
                            on_clear_lims_password=on_clear_lims_password,
                            lims_password_present=lims_password_present,
                        )
                    section_bodies[section] = body

        def _do_save(_evt: Any = None) -> None:
            if on_save is None:
                return
            try:
                validated = finalize_settings_draft(draft)
            except ValidationError as exc:
                first_error = exc.errors()[0]
                loc = ".".join(str(p) for p in first_error.get("loc", ()))
                notifications.notify_error(
                    f"Config invalid ({loc}): {first_error.get('msg', 'validation failed')}"
                )
                return
            on_save(validated)

        with (
            ui.row()
            .classes("items-center w-full justify-end")
            .style(
                "gap: var(--sp-3); padding-top: var(--sp-4);",
            )
        ):
            ui.button(
                "Discard all",
                on_click=lambda _evt: on_discard(s) if on_discard else None,
            ).props('flat data-testid="settings-discard"')
            ui.button(
                save_button_label(s),
                on_click=_do_save,
            ).props('color=primary data-testid="settings-save"')
    return card


def _render_section_body(
    section: str,
    draft: Config,
    *,
    on_save_lims_password: Callable[[str], None] | None = None,
    on_clear_lims_password: Callable[[], None] | None = None,
    lims_password_present: bool = False,
) -> None:
    """Render the content for a single section, bound to ``draft``.

    Every scalar field uses NiceGUI two-way binding against the
    corresponding ``draft.<sub-block>`` attribute, so edits accumulate
    on the draft and ``render_settings_page``'s Save handler can
    re-validate and emit the finished :class:`Config`. The list-valued
    sections (equipment, scanned extensions) render their current
    entries read-only -- rich list editors are a follow-up; the
    deadlock this unblocks is the scalar config fields.

    The LIMS section's password credential is the exception to the
    draft-binding rule: it is keyring-backed and writes at click time
    via ``on_save_lims_password`` / ``on_clear_lims_password``.
    """

    from nicegui import ui

    with (
        ui.column()
        .classes("w-full")
        .props(f'data-testid="settings-section-{section}"')
        .style("gap: 0.5rem; padding: 0 1rem;")
    ):
        ui.label(SECTION_TITLES[section]).style(
            "font-family: var(--font-display); "
            "font-size: var(--text-md); "
            "color: var(--color-heading); "
            "font-weight: 600;"
        )

        if section == "paths":
            ui.input(label="Templates directory", value=draft.paths.templates_dir).props(
                'data-testid="settings-paths-templates"'
            ).bind_value(draft.paths, "templates_dir")
            ui.input(label="Plugin directory", value=draft.paths.plugin_dir).props(
                'data-testid="settings-paths-plugin"'
            ).bind_value(draft.paths, "plugin_dir")
            ui.input(label="Local data root", value=draft.paths.local_root).props(
                'data-testid="settings-paths-local-root"'
            ).bind_value(draft.paths, "local_root")
        elif section == "lims":
            ui.input(label="Endpoint URL", value=draft.lims.endpoint).props(
                'data-testid="settings-lims-endpoint"'
            ).bind_value(draft.lims, "endpoint")
            ui.input(label="Operator email", value=draft.lims.email).props(
                'data-testid="settings-lims-email"'
            ).bind_value(draft.lims, "email")
            credential_field.credential_field(
                label="LIMS password",
                on_save=on_save_lims_password or (lambda _value: None),
                on_clear=on_clear_lims_password or (lambda: None),
                initial_state=lims_credential_initial_state(present=lims_password_present),
                data_testid="settings-lims-password",
            )
            ui.number(label="Cache TTL (hours)", value=draft.lims.cache_ttl_hours).props(
                'data-testid="settings-lims-cache-ttl"'
            ).bind_value(draft.lims, "cache_ttl_hours")
            ui.input(label="Offline catalogue path", value=draft.lims.offline_catalogue_path).props(
                'data-testid="settings-lims-offline-path"'
            ).bind_value(draft.lims, "offline_catalogue_path")
            test_connection_panel.test_connection_panel(None)
        elif section == "equipment":
            _render_equipment_section(draft)
        elif section == "nas_cleanup":
            ui.checkbox("Cleanup enabled", value=draft.nas_cleanup.enabled).bind_value(
                draft.nas_cleanup, "enabled"
            )
            ui.number(
                label="Minimum verify passes", value=draft.nas_cleanup.min_verify_passes
            ).bind_value(draft.nas_cleanup, "min_verify_passes")
            ui.number(
                label="Minimum age (hours)", value=draft.nas_cleanup.min_age_hours
            ).bind_value(draft.nas_cleanup, "min_age_hours")
            ui.checkbox(
                "Retain .exlab-wizard/ metadata", value=draft.nas_cleanup.retain_cache
            ).bind_value(draft.nas_cleanup, "retain_cache")
        elif section == "validator":
            ui.number(
                label="Max content-scan size (MiB)",
                value=draft.validator.content_scan_max_mib,
            ).bind_value(draft.validator, "content_scan_max_mib")
            ui.label(
                "Scanned file extensions: " + ", ".join(draft.validator.content_scan_extensions)
            )
        elif section == "logging":
            ui.radio(["DEBUG", "INFO", "WARN", "ERROR"], value=draft.logging.level).bind_value(
                draft.logging, "level"
            )
            ui.number(
                label="Central log size cap (MB)", value=draft.logging.central_log_max_mb
            ).bind_value(draft.logging, "central_log_max_mb")
            ui.number(
                label="Rotated log copies kept", value=draft.logging.central_log_keep
            ).bind_value(draft.logging, "central_log_keep")
        elif section == "orchestrator":
            # Redesign §3.1: orchestrator pipeline is always active; the
            # enabled toggle is removed. label + staging_root are now
            # always required (they join the setup-incomplete gate). The
            # full Settings refactor that folds these into an early
            # section lands in Phase 6.
            ui.input(label="Workstation label", value=draft.orchestrator.label).bind_value(
                draft.orchestrator, "label"
            )
            ui.input(label="Staging root", value=draft.orchestrator.staging_root).bind_value(
                draft.orchestrator, "staging_root"
            )
        elif section == "application":
            # "Start at login" is the autostart toggle, not a config.yaml
            # field -- it is set from the welcome card. Shown here for
            # discoverability; wiring it is a follow-up.
            ui.checkbox("Start ExLab-Wizard at login")
            ui.label("Show in system tray: available")
            ui.button("Quit ExLab-Wizard now").props("flat")


# Redesign §6: the canonical equipment-config assembler now lives in
# ``ui/equipment_form`` so both the wizard and Settings can share it.
# This module re-exports it for backward compatibility with existing
# callers / imports.
from exlab_wizard.ui.equipment_form import build_equipment_config  # noqa: E402


def _render_equipment_section(draft: Config) -> None:
    """Render the equipment list + a full add-equipment sub-form.

    Adding an entry appends a validated :class:`EquipmentConfig` to
    ``draft.equipment`` and reflects it in the visible list; the whole
    draft is re-validated and persisted when the operator clicks Save.

    The sub-form covers the full §9 equipment surface: a
    completeness-signal radio (``sentinel_file`` / ``manifest``) that
    swaps the filename field, and a transport radio (``rclone`` /
    ``rsync_ssh``) that swaps the transport fieldset.
    """
    from nicegui import ui

    rows = ui.column().classes("w-full").style("gap: 0.25rem;")

    def _render_rows() -> None:
        rows.clear()
        with rows:
            if draft.equipment:
                for entry in draft.equipment:
                    transport_summary = (
                        entry.transport.type if entry.transport is not None else "stage"
                    )
                    ui.label(
                        f"{entry.id} -- {entry.label} "
                        f"[{entry.completeness_signal} / {transport_summary}]"
                    ).props('data-testid="settings-equipment-row"')
            else:
                ui.label("No equipment configured yet.").props(
                    'data-testid="settings-equipment-empty"'
                )

    _render_rows()

    eq_id = ui.input(label="Equipment ID (^[A-Z][A-Z0-9_]*$)").props(
        'data-testid="settings-equipment-id"'
    )
    eq_label = ui.input(label="Label").props('data-testid="settings-equipment-label"')
    eq_local = ui.input(label="Local root").props('data-testid="settings-equipment-local-root"')
    eq_nas = ui.input(label="NAS root").props('data-testid="settings-equipment-nas-root"')

    # Completeness signal: a radio that swaps the filename field.
    signal_radio = ui.radio(
        [CompletenessSignal.SENTINEL_FILE.value, CompletenessSignal.MANIFEST.value],
        value=CompletenessSignal.SENTINEL_FILE.value,
    ).props('data-testid="settings-equipment-signal"')
    # Widget refs the swap-panels and ``_add`` share.
    fields: dict[str, Any] = {}

    @ui.refreshable
    def _signal_field() -> None:
        if signal_radio.value == CompletenessSignal.MANIFEST.value:
            fields["manifest"] = ui.input(label="Manifest filename", value="manifest.json").props(
                'data-testid="settings-equipment-manifest"'
            )
            fields.pop("sentinel", None)
        else:
            fields["sentinel"] = ui.input(
                label="Sentinel filename", value="acquisition_complete.flag"
            ).props('data-testid="settings-equipment-sentinel"')
            fields.pop("manifest", None)

    _signal_field()
    signal_radio.on_value_change(lambda _e: _signal_field.refresh())

    # Transport: a radio that swaps the transport fieldset.
    transport_radio = ui.radio(["rclone", "rsync_ssh"], value="rclone").props(
        'data-testid="settings-equipment-transport"'
    )

    @ui.refreshable
    def _transport_fields() -> None:
        if transport_radio.value == "rsync_ssh":
            fields["ssh_target"] = ui.input(label="SSH target").props(
                'data-testid="settings-equipment-ssh-target"'
            )
            fields["ssh_key"] = ui.input(label="SSH key path", value="~/.ssh/id_ed25519").props(
                'data-testid="settings-equipment-ssh-key"'
            )
            fields["rsync_path"] = ui.input(label="Remote path").props(
                'data-testid="settings-equipment-rsync-path"'
            )
            for stale in ("rclone_remote", "rclone_path"):
                fields.pop(stale, None)
        else:
            fields["rclone_remote"] = ui.input(label="rclone remote").props(
                'data-testid="settings-equipment-rclone-remote"'
            )
            fields["rclone_path"] = ui.input(label="rclone remote path").props(
                'data-testid="settings-equipment-rclone-path"'
            )
            for stale in ("ssh_target", "ssh_key", "rsync_path"):
                fields.pop(stale, None)

    _transport_fields()
    transport_radio.on_value_change(lambda _e: _transport_fields.refresh())

    def _add(_evt: Any = None) -> None:
        try:
            entry = build_equipment_config(
                equipment_id=eq_id.value or "",
                label=eq_label.value or "",
                local_root=eq_local.value or "",
                nas_root=eq_nas.value or "",
                completeness_signal=signal_radio.value or CompletenessSignal.SENTINEL_FILE.value,
                sentinel_filename=(fields["sentinel"].value or "" if "sentinel" in fields else ""),
                manifest_filename=(fields["manifest"].value or "" if "manifest" in fields else ""),
                transport_type=transport_radio.value or "rclone",
                rclone_remote=(
                    fields["rclone_remote"].value or "" if "rclone_remote" in fields else ""
                ),
                rclone_remote_path=(
                    fields["rclone_path"].value or "" if "rclone_path" in fields else ""
                ),
                ssh_target=(fields["ssh_target"].value or "" if "ssh_target" in fields else ""),
                ssh_key_path=(fields["ssh_key"].value or "" if "ssh_key" in fields else ""),
                rsync_remote_path=(
                    fields["rsync_path"].value or "" if "rsync_path" in fields else ""
                ),
            )
        except Exception as exc:
            notifications.notify_error(f"Equipment invalid: {exc}")
            return
        if any(e.id == entry.id for e in draft.equipment):
            notifications.notify_error(f"Equipment {entry.id!r} already exists")
            return
        draft.equipment.append(entry)
        _render_rows()
        for widget in (eq_id, eq_label, eq_local, eq_nas):
            widget.value = ""
        notifications.notify_success(f"Equipment {entry.id!r} added")

    ui.button("Add equipment", on_click=_add).props('data-testid="settings-equipment-add"')
