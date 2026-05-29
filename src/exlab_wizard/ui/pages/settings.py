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
from exlab_wizard.logging import get_logger
from exlab_wizard.paths import suggested_staging_root
from exlab_wizard.ui import notifications
from exlab_wizard.ui.components import credential_field, test_connection_panel

_log = get_logger(__name__)


SETTINGS_SECTIONS: tuple[str, ...] = (
    "paths",
    "lims",
    "equipment",
    "nas_cleanup",
    # "operators" backs OperatorsConfig.allowlist (Frontend §7.9). It is a
    # chip editor and is non-gating: the allowlist defaults to [] (any
    # operator allowed) and it is never added to ``_missing_setup_sections``.
    "operators",
    "validator",
    "logging",
    "orchestrator",
    "application",
)

# Rclone-only NAS sync migration (2026-05-26). The NAS-credentials
# section is *not* part of the canonical onboarding-order constant
# (``SETTINGS_SECTIONS`` stays at the original eight); it is inserted
# dynamically after ``equipment`` by :func:`settings_sections_for` only
# when password-requiring nas-mode equipment exists.
NAS_CREDENTIALS_SECTION = "nas_credentials"

SECTION_TITLES: dict[str, str] = {
    "paths": "Paths",
    "lims": "LIMS",
    "equipment": "Equipment List",
    NAS_CREDENTIALS_SECTION: "NAS Credentials",
    "nas_cleanup": "NAS Cleanup",
    "operators": "Operators",
    "validator": "Validator",
    "logging": "Logging",
    "orchestrator": "Orchestrator Mode",
    "application": "Application",
}


def _password_requiring_nas_equipment(config: Config | None) -> list[Any]:
    """Return nas-mode equipment whose transport sources a keyring password.

    Shared by :func:`settings_sections_for` (visibility) and the
    NAS-credentials section renderer (one row per entry).
    """
    if config is None:
        return []
    from exlab_wizard.config.models import transport_requires_keyring_password
    from exlab_wizard.constants import SyncMode

    return [
        eq
        for eq in config.equipment
        if eq.sync_mode == SyncMode.NAS and transport_requires_keyring_password(eq.transport)
    ]


def settings_sections_for(config: Config | None) -> tuple[str, ...]:
    """Return the visible section ids for ``config``.

    The NAS-credentials section is inserted right after ``equipment``
    only when at least one nas-mode equipment requires a keyring
    password; otherwise the canonical :data:`SETTINGS_SECTIONS` order is
    returned unchanged (so a stage-only / no-equipment install never
    sees an empty credentials pane).
    """
    if not _password_requiring_nas_equipment(config):
        return SETTINGS_SECTIONS
    out: list[str] = []
    for section in SETTINGS_SECTIONS:
        out.append(section)
        if section == "equipment":
            out.append(NAS_CREDENTIALS_SECTION)
    return tuple(out)


@dataclass
class SettingsState:
    """Mutable state for the dialog."""

    active_section: str = "paths"
    incomplete_sections: tuple[str, ...] = ()
    dirty_sections: set[str] = field(default_factory=set)
    pending_change_count: int = 0


def first_incomplete_section(incomplete: tuple[str, ...]) -> str | None:
    """Return the first section ID in canonical order that's incomplete.

    The dynamic NAS-credentials section is not part of the static
    :data:`SETTINGS_SECTIONS` tuple, so it is folded into the canonical
    order here (right after ``equipment``) -- otherwise an
    ``INCOMPLETE_NO_NAS_CREDENTIAL`` install would auto-select nothing
    and land the operator on the default section (rclone-only migration,
    2026-05-26).
    """
    order: list[str] = []
    for section in SETTINGS_SECTIONS:
        order.append(section)
        if section == "equipment":
            order.append(NAS_CREDENTIALS_SECTION)
    for section in order:
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
    nas_password_present_for: Callable[[str], bool] | None = None,
    nas_credential_handlers: Callable[[str], tuple[Callable[[str], None], Callable[[], None]]]
    | None = None,
    on_test_equipment: Callable[[str], Any] | None = None,
    autostart_registered: bool = False,
    on_set_autostart: Callable[[bool], bool | None] | None = None,
    on_quit: Callable[[], None] | None = None,
    tray_available: bool = False,
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

    The NAS-credentials hooks (rclone-only migration, 2026-05-26) mirror
    that contract per equipment: ``nas_password_present_for(id)`` seeds
    each row's resting state, ``nas_credential_handlers(id)`` returns the
    ``(on_save, on_clear)`` pair the row writes through, and
    ``on_test_equipment(id)`` runs the rclone probe and returns a
    :class:`TestConnectionResult` (or an awaitable of one) for the inline
    panel. All three are optional so unit tests can render the section
    without a wired keyring.
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

    # Section visibility is draft-derived: the NAS-credentials section
    # appears only when password-requiring nas-mode equipment exists.
    sections = settings_sections_for(draft)

    payload = {
        "active": s.active_section,
        "save_label": save_button_label(s),
        "sections": list(sections),
        "warnings": [section for section in sections if section_has_warning(s, section)],
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
                for section in sections:
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
                for section in sections:
                    body = ui.column().classes("w-full")
                    body.visible = section == s.active_section
                    with body:
                        _render_section_body(
                            section,
                            draft,
                            on_save_lims_password=on_save_lims_password,
                            on_clear_lims_password=on_clear_lims_password,
                            lims_password_present=lims_password_present,
                            nas_password_present_for=nas_password_present_for,
                            nas_credential_handlers=nas_credential_handlers,
                            on_test_equipment=on_test_equipment,
                            autostart_registered=autostart_registered,
                            on_set_autostart=on_set_autostart,
                            on_quit=on_quit,
                            tray_available=tray_available,
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


def _render_chip_editor(
    values: list[str],
    *,
    add_label: str,
    testid: str,
    validate: Callable[[str], str | None] | None = None,
    on_reset: Callable[[], None] | None = None,
    reset_label: str = "Reset to defaults",
    empty_text: str = "(none)",
) -> None:
    """Reusable chip / list editor bound to a draft string list (T7 / T10).

    Mutates ``values`` in place -- ``[+ Add]`` appends (rejecting blanks,
    duplicates, and ``validate`` failures), each chip carries a delete, and
    an optional ``[Reset]`` replaces the contents -- so persistence rides
    the existing draft -> ``finalize_settings_draft`` -> Save path with no
    new plumbing. Entries are stored verbatim (case-sensitive, no
    lowercasing); whitespace is trimmed on add.
    """
    from nicegui import ui

    chips = ui.row().classes("items-center w-full").style("gap: 0.35rem; flex-wrap: wrap;")

    def _render_chips() -> None:
        chips.clear()
        with chips:
            if not values:
                ui.label(empty_text).props(f'data-testid="{testid}-empty"').style(
                    "color: var(--color-muted);"
                )
            for idx, value in enumerate(values):
                with (
                    ui.row()
                    .classes("items-center")
                    .props(f'data-testid="{testid}-chip"')
                    .style(
                        "gap: 0.15rem; background: var(--color-rule); "
                        "border-radius: var(--radius-sm); padding: 0.05rem 0.1rem 0.05rem 0.5rem;"
                    )
                ):
                    ui.label(value).style(
                        "font-family: var(--font-mono); font-size: var(--text-xs);"
                    )
                    ui.button(icon="close", on_click=lambda _e, i=idx: _remove(i)).props(
                        "flat dense round size=sm"
                    )

    def _remove(idx: int) -> None:
        if 0 <= idx < len(values):
            del values[idx]
            _render_chips()

    _render_chips()

    new_input = ui.input(label=add_label).props(f'data-testid="{testid}-input"')

    def _add() -> None:
        raw = (new_input.value or "").strip()
        if not raw:
            return
        if validate is not None:
            error = validate(raw)
            if error is not None:
                notifications.notify_error(error)
                return
        if raw not in values:
            values.append(raw)
            _render_chips()
        new_input.value = ""

    with ui.row().classes("items-center").style("gap: 0.5rem;"):
        ui.button("+ Add", on_click=lambda _e: _add()).props(f'flat data-testid="{testid}-add"')
        if on_reset is not None:

            def _reset() -> None:
                on_reset()
                _render_chips()

            ui.button(reset_label, on_click=lambda _e: _reset()).props(
                f'flat data-testid="{testid}-reset"'
            )


def _render_section_body(
    section: str,
    draft: Config,
    *,
    on_save_lims_password: Callable[[str], None] | None = None,
    on_clear_lims_password: Callable[[], None] | None = None,
    lims_password_present: bool = False,
    nas_password_present_for: Callable[[str], bool] | None = None,
    nas_credential_handlers: Callable[[str], tuple[Callable[[str], None], Callable[[], None]]]
    | None = None,
    on_test_equipment: Callable[[str], Any] | None = None,
    autostart_registered: bool = False,
    on_set_autostart: Callable[[bool], bool | None] | None = None,
    on_quit: Callable[[], None] | None = None,
    tray_available: bool = False,
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
        elif section == NAS_CREDENTIALS_SECTION:
            _render_nas_credentials_section(
                draft,
                nas_password_present_for=nas_password_present_for or (lambda _id: False),
                nas_credential_handlers=nas_credential_handlers,
                on_test_equipment=on_test_equipment,
            )
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
        elif section == "operators":
            # Frontend §7.9: empty allowlist = any operator; non-empty = the
            # wizard renders a dropdown of these names and rejects free-text.
            # Case-sensitive (OperatorsConfig is str_strip_whitespace, not
            # lowercased) and non-gating.
            ui.label(
                "If empty, the operator field accepts any value. If non-empty, the wizard "
                "shows a dropdown of these names and rejects free-text."
            ).style("color: var(--color-muted); font-size: var(--text-sm);")
            _render_chip_editor(
                draft.operators.allowlist,
                add_label="Add operator username",
                testid="settings-operators",
                empty_text="Any operator allowed (allowlist empty)",
            )
        elif section == "validator":
            ui.number(
                label="Max content-scan size (MiB)",
                value=draft.validator.content_scan_max_mib,
            ).bind_value(draft.validator, "content_scan_max_mib")
            ui.label("Scanned file extensions").style("color: var(--color-body);")

            def _reset_extensions() -> None:
                from exlab_wizard.config.models import _default_content_scan_extensions

                draft.validator.content_scan_extensions[:] = _default_content_scan_extensions()

            _render_chip_editor(
                draft.validator.content_scan_extensions,
                add_label="Add extension (e.g. .txt)",
                testid="settings-scan-ext",
                validate=lambda v: None if v.startswith(".") else "Extensions must start with '.'",
                on_reset=_reset_extensions,
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
            # ``label`` is required (it identifies this workstation in every
            # run's creation.json). ``staging_root`` is opt-in: blank means
            # this device is not a staging PC. The placeholder shows an
            # OS-appropriate suggestion without prefilling the value -- the
            # directory is created only when a non-empty path is saved (see
            # ui.mount._persist_config).
            ui.input(label="Workstation label", value=draft.orchestrator.label).bind_value(
                draft.orchestrator, "label"
            )
            ui.input(
                label="Staging root (optional)",
                value=draft.orchestrator.staging_root,
                placeholder=str(suggested_staging_root()),
            ).bind_value(draft.orchestrator, "staging_root")
        elif section == "application":
            # "Start at login" (T8): applied immediately (NOT draft-bound,
            # §7.13). Seeded from the real registration state; on toggle it
            # reflects the actual post-op ``is_registered()`` and reverts on
            # failure. Disabled when no toggle is wired (headless/tests).
            _guard = {"busy": False}
            box_holder: dict[str, Any] = {}

            def _on_autostart(event: Any) -> None:
                if _guard["busy"] or on_set_autostart is None:
                    return
                actual = on_set_autostart(bool(event.value))
                box = box_holder.get("box")
                if actual is not None and box is not None and bool(actual) != bool(event.value):
                    _guard["busy"] = True
                    try:
                        box.value = bool(actual)
                    finally:
                        _guard["busy"] = False

            autostart_box = ui.checkbox(
                "Start ExLab-Wizard at login",
                value=autostart_registered,
                on_change=_on_autostart,
            ).props('data-testid="settings-autostart"')
            box_holder["box"] = autostart_box
            if on_set_autostart is None:
                autostart_box.props("disable")

            # Real tray availability + window-on-close behavior (T11, §7.13).
            tray_text = "available" if tray_available else "unavailable (window-only)"
            ui.label(f"Show in system tray: {tray_text}").props(
                'data-testid="settings-tray-status"'
            )
            ui.label(
                "Closing the window keeps ExLab-Wizard running in the tray; "
                "use Quit to exit completely."
            ).style("color: var(--color-muted); font-size: var(--text-sm);")

            # "Quit ExLab-Wizard now" (T9): graceful shutdown behind a confirm,
            # scheduled non-blocking by the host. Disabled when no hook wired.
            quit_btn = ui.button("Quit ExLab-Wizard now").props(
                'flat data-testid="settings-quit"'
            )
            if on_quit is None:
                quit_btn.props("disable")
            else:

                def _confirm_quit() -> None:
                    confirm = ui.dialog()
                    with (
                        confirm,
                        ui.card().props('data-testid="settings-quit-dialog"'),
                    ):
                        ui.label("Quit ExLab-Wizard?").style("font-weight: 600;")
                        ui.label(
                            "In-flight operations are allowed to finish first."
                        ).style("color: var(--color-muted);")

                        def _do_quit() -> None:
                            confirm.close()
                            on_quit()

                        with ui.row().classes("justify-end w-full").style("gap: 0.5rem;"):
                            ui.button("Cancel", on_click=lambda _e: confirm.close()).props("flat")
                            ui.button("Quit", on_click=lambda _e: _do_quit()).props(
                                'color=negative data-testid="settings-quit-confirm"'
                            )
                    confirm.open()

                quit_btn.on("click", lambda _e: _confirm_quit())


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

    The sub-form covers the full §9 equipment surface: a transport
    radio (``rclone_sftp`` / ``rclone_smb``) that swaps the transport
    fieldset. Both options are password-based and route through rclone;
    the per-equipment password lives in the OS keyring and is entered in
    the NAS-credentials section below this one.
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
                    ui.label(f"{entry.id} -- {entry.label} [{transport_summary}]").props(
                        'data-testid="settings-equipment-row"'
                    )
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

    # Widget refs the swap-panels and ``_add`` share.
    fields: dict[str, Any] = {}

    # Transport: a radio that swaps the transport fieldset. Both options
    # use rclone under the hood; the password lives in the keyring and is
    # set in the NAS-credentials section after the row is saved.
    transport_radio = ui.radio(["rclone_sftp", "rclone_smb"], value="rclone_sftp").props(
        'data-testid="settings-equipment-transport"'
    )

    @ui.refreshable
    def _transport_fields() -> None:
        if transport_radio.value == "rclone_smb":
            fields["smb_host"] = ui.input(label="SMB host").props(
                'data-testid="settings-equipment-smb-host"'
            )
            fields["smb_share"] = ui.input(label="SMB share").props(
                'data-testid="settings-equipment-smb-share"'
            )
            fields["smb_user"] = ui.input(label="SMB user").props(
                'data-testid="settings-equipment-smb-user"'
            )
            fields["smb_domain"] = ui.input(label="SMB domain (optional)").props(
                'data-testid="settings-equipment-smb-domain"'
            )
            fields["smb_remote_path"] = ui.input(label="Remote subpath (optional)").props(
                'data-testid="settings-equipment-smb-remote-path"'
            )
            for stale in ("sftp_host", "sftp_port", "sftp_user", "sftp_remote_path"):
                fields.pop(stale, None)
        else:
            fields["sftp_host"] = ui.input(label="SFTP host").props(
                'data-testid="settings-equipment-sftp-host"'
            )
            fields["sftp_port"] = ui.number(label="SFTP port", value=22).props(
                'data-testid="settings-equipment-sftp-port"'
            )
            fields["sftp_user"] = ui.input(label="SFTP user").props(
                'data-testid="settings-equipment-sftp-user"'
            )
            fields["sftp_remote_path"] = ui.input(label="Remote path").props(
                'data-testid="settings-equipment-sftp-remote-path"'
            )
            for stale in ("smb_host", "smb_share", "smb_user", "smb_domain", "smb_remote_path"):
                fields.pop(stale, None)

    _transport_fields()
    transport_radio.on_value_change(lambda _e: _transport_fields.refresh())

    def _field(name: str) -> str:
        return (fields[name].value or "") if name in fields else ""

    def _add(_evt: Any = None) -> None:
        try:
            entry = build_equipment_config(
                equipment_id=eq_id.value or "",
                label=eq_label.value or "",
                local_root=eq_local.value or "",
                nas_root=eq_nas.value or "",
                transport_type=transport_radio.value or "rclone_sftp",
                sftp_host=_field("sftp_host"),
                sftp_port=int(fields["sftp_port"].value or 22) if "sftp_port" in fields else 22,
                sftp_user=_field("sftp_user"),
                sftp_remote_path=_field("sftp_remote_path"),
                smb_host=_field("smb_host"),
                smb_share=_field("smb_share"),
                smb_user=_field("smb_user"),
                smb_domain=_field("smb_domain"),
                smb_remote_path=_field("smb_remote_path"),
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


def _render_nas_credentials_section(
    draft: Config,
    *,
    nas_password_present_for: Callable[[str], bool],
    nas_credential_handlers: Callable[[str], tuple[Callable[[str], None], Callable[[], None]]]
    | None,
    on_test_equipment: Callable[[str], Any] | None,
) -> None:
    """Render one keyring-credential row + Test-connection panel per equipment.

    Rclone-only NAS sync migration (2026-05-26). Each nas-mode
    equipment whose transport requires a password gets its own row:
    a :func:`credential_field` writing straight to the keyring (via the
    per-equipment ``(on_save, on_clear)`` pair from
    ``nas_credential_handlers``) plus a "Test connection" button that
    runs the rclone probe and renders the result inline.

    The handler closures are built per equipment id so a Save in one
    row never writes another equipment's keyring slot.
    """
    import inspect

    from nicegui import ui

    equipment = _password_requiring_nas_equipment(draft)
    if not equipment:
        ui.label("No NAS-mode equipment requires a password.").props(
            'data-testid="settings-nas-credentials-empty"'
        )
        return

    ui.label(
        "Each NAS-mode equipment authenticates with a password stored in the OS keyring. "
        "Set it here, then use Test connection to confirm the NAS is reachable."
    ).style("font-size: var(--text-sm); color: var(--color-muted);")

    for eq in equipment:
        with (
            ui.column()
            .classes("w-full")
            .props(f'data-testid="settings-nas-credential-row-{eq.id}"')
            .style("gap: 0.25rem; padding-bottom: 0.75rem;")
        ):
            if nas_credential_handlers is not None:
                on_save, on_clear = nas_credential_handlers(eq.id)
            else:
                on_save, on_clear = (lambda _value: None), (lambda: None)
            credential_field.credential_field(
                label=f"NAS password — {eq.id}",
                on_save=on_save,
                on_clear=on_clear,
                initial_state=lims_credential_initial_state(
                    present=nas_password_present_for(eq.id)
                ),
                data_testid=f"settings-nas-password-{eq.id}",
            )
            panel = ui.column().classes("w-full")

            def _make_test(equipment_id: str, container: Any) -> Callable[[], Any]:
                async def _test() -> None:
                    container.clear()
                    if on_test_equipment is None:
                        return
                    result = on_test_equipment(equipment_id)
                    if inspect.isawaitable(result):
                        result = await result
                    with container:
                        test_connection_panel.test_connection_panel(result)

                return _test

            ui.button("Test connection", on_click=_make_test(eq.id, panel)).props(
                f'flat data-testid="settings-nas-test-{eq.id}"'
            )
