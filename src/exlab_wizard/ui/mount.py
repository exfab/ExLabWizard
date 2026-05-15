"""NiceGUI mount helper. Backend Spec §4.3, §15.3.2.

``mount_ui`` is the single entry point the tray calls after
``create_app`` returns: it registers ``@ui.page(...)`` handlers for every
wizard route and binds the NiceGUI ASGI sub-app onto the FastAPI app at
``/`` via ``ui.run_with``. Page handlers pull live components from
``app.state.dependencies`` -- the API surface and the GUI share the same
dependency bundle.

The handlers are deliberately defensive: every dependency access is
wrapped in try/except so a half-wired backend (LIMS not reachable, sync
queue absent, validator not yet vetted) degrades to a structured
"unavailable" banner instead of leaking a stack trace into pywebview.
The factory in :mod:`exlab_wizard.tray.dependencies` follows the same
pattern at construction time; the two layers together let the operator
see a usable GUI even when individual collaborators are unavailable.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from exlab_wizard.constants import KEYRING_USERNAME_LIMS, AuditScopeKind, RunKind
from exlab_wizard.logging import get_logger

if TYPE_CHECKING:
    from fastapi import FastAPI


__all__ = ["MOUNT_PATH", "mount_ui"]

_log = get_logger(__name__)

MOUNT_PATH = "/"


def mount_ui(app: FastAPI, *, storage_secret: str) -> None:
    """Register every wizard page on ``app`` and mount NiceGUI at ``/``.

    ``storage_secret`` is the per-installation token from
    :mod:`exlab_wizard.tray.storage_secret`; NiceGUI uses it to sign the
    Starlette ``SessionMiddleware`` cookie that backs
    ``app.storage.user``. The codebase doesn't read ``app.storage.*``
    today but NiceGUI refuses to mount without a non-empty value.
    """
    from nicegui import ui

    from exlab_wizard.ui.theme import register_static_assets

    register_static_assets()
    _register_pages(app, ui)
    ui.run_with(
        app,
        mount_path=MOUNT_PATH,
        show_welcome_message=False,
        storage_secret=storage_secret,
    )


def _register_pages(app: FastAPI, ui: Any) -> None:
    """Define every ``@ui.page(...)`` handler. Called from :func:`mount_ui`."""

    from exlab_wizard.ui.pages import (
        main as main_page,
    )
    from exlab_wizard.ui.pages import (
        problems as problems_page,
    )
    from exlab_wizard.ui.pages import (
        settings as settings_page,
    )
    from exlab_wizard.ui.pages import (
        staging as staging_page,
    )
    from exlab_wizard.ui.pages import (
        templates as templates_page,
    )
    from exlab_wizard.ui.pages import (
        welcome as welcome_page,
    )
    from exlab_wizard.ui.pages import (
        wizard_equipment as wizard_equipment_page,
    )
    from exlab_wizard.ui.pages import (
        wizard_project as wizard_project_page,
    )

    def _deps() -> Any:
        return getattr(app.state, "dependencies", None)

    @ui.page("/")
    def _index() -> Any:
        deps = _deps()
        if _restart_gate(deps, ui):
            return
        if _is_setup_ready(deps):
            ui.navigate.to("/main")
        else:
            ui.navigate.to("/welcome")

    @ui.page("/restart-required")
    def _restart_required() -> Any:
        # Terminal screen: config.yaml was written but the tray's
        # config-dependent components were built once at boot, so the
        # operator must relaunch to finish setup. Not gated -- this is
        # the gate's destination.
        return _render_restart_required(ui)

    @ui.page("/welcome")
    def _welcome() -> Any:
        if _restart_gate(_deps(), ui):
            return None

        def _on_started(autostart: bool) -> None:
            _apply_autostart(_deps(), autostart)
            ui.navigate.to("/settings")

        def _on_skip(autostart: bool) -> None:
            _apply_autostart(_deps(), autostart)
            ui.navigate.to("/main")

        return welcome_page.render_welcome_page(
            on_get_started=_on_started,
            on_skip=_on_skip,
        )

    @ui.page("/main")
    def _main() -> Any:
        deps = _deps()
        if _restart_gate(deps, ui):
            return None
        state = _build_main_state(deps)

        def _refresh() -> None:
            ui.navigate.to("/main")

        return main_page.render_main_page(
            on_open_new_project=lambda: ui.navigate.to("/wizard/project"),
            on_open_new_run=lambda: ui.navigate.to("/wizard/run"),
            on_open_new_test_run=lambda: ui.navigate.to("/wizard/test-run"),
            on_open_settings=lambda: ui.navigate.to("/settings"),
            on_refresh=_refresh,
            state=state,
        )

    @ui.page("/wizard/project")
    async def _wizard_project() -> Any:
        deps = _deps()
        if _restart_gate(deps, ui):
            return None
        return wizard_project_page.render_project_wizard(
            templates=_template_names(deps, "project"),
            equipment_ids=_equipment_ids(deps),
            template_questions=_template_questions_map(deps, "project"),
            lims_projects=await _lims_projects(deps),
            on_submit=lambda state: _submit_project(deps, state, ui),
            on_cancel=lambda: ui.navigate.to("/main"),
        )

    @ui.page("/wizard/run")
    def _wizard_run() -> Any:
        deps = _deps()
        if _restart_gate(deps, ui):
            return None
        return _render_run_wizard(deps, RunKind.EXPERIMENTAL, ui)

    @ui.page("/wizard/test-run")
    def _wizard_test_run() -> Any:
        deps = _deps()
        if _restart_gate(deps, ui):
            return None
        return _render_run_wizard(deps, RunKind.TEST, ui)

    @ui.page("/wizard/equipment")
    def _wizard_equipment() -> Any:
        """Redesign §6 — Add-Equipment wizard route."""
        deps = _deps()
        if _restart_gate(deps, ui):
            return None
        state = wizard_equipment_page.EquipmentWizardState()

        def _on_advance(current_step: str) -> None:
            idx = wizard_equipment_page.EQUIPMENT_WIZARD_STEPS.index(current_step)
            if idx + 1 < len(wizard_equipment_page.EQUIPMENT_WIZARD_STEPS):
                state.active_step = wizard_equipment_page.EQUIPMENT_WIZARD_STEPS[idx + 1]
                ui.navigate.to("/wizard/equipment")

        def _on_back(current_step: str) -> None:
            idx = wizard_equipment_page.EQUIPMENT_WIZARD_STEPS.index(current_step)
            if idx > 0:
                state.active_step = wizard_equipment_page.EQUIPMENT_WIZARD_STEPS[idx - 1]
                ui.navigate.to("/wizard/equipment")

        def _on_confirm(eq: Any) -> None:
            # Posts through the config router. The actual HTTP wiring is
            # supplied by the deps' append-equipment callable; tests can
            # stub it.
            append = getattr(deps, "append_equipment", None) if deps is not None else None
            if append is not None:
                try:
                    append(eq)
                except Exception as exc:
                    _show_toast(ui, f"Could not add equipment: {exc}", positive=False)
                    return
            ui.navigate.to("/main")

        return wizard_equipment_page.render_wizard_equipment(
            state=state,
            on_advance=_on_advance,
            on_back=_on_back,
            on_confirm=_on_confirm,
            on_cancel=lambda: ui.navigate.to("/main"),
        )

    @ui.page("/templates")
    def _templates() -> Any:
        deps = _deps()
        if _restart_gate(deps, ui):
            return None
        templates_dir = _templates_dir(deps)

        def _on_create(
            name: str, template_type: str, description: str, run_scope: str | None
        ) -> None:
            if templates_dir is None:
                _show_toast(ui, "Set the templates directory in Settings first", positive=False)
                return
            try:
                templates_page.create_template(
                    templates_dir,
                    name=name,
                    template_type=template_type,
                    description=description,
                    run_scope=run_scope,
                )
            except Exception as exc:
                _show_toast(ui, f"Template not created: {exc}", positive=False)
                return
            _show_toast(ui, f"Template {name!r} created", positive=True)
            ui.navigate.to("/templates")

        summaries = (
            templates_page.list_templates(templates_dir) if templates_dir is not None else []
        )
        return templates_page.render_template_manager(
            templates=summaries,
            on_create=_on_create,
            on_back=lambda: ui.navigate.to("/main"),
        )

    @ui.page("/settings")
    def _settings(active: str = "") -> Any:
        deps = _deps()
        if _restart_gate(deps, ui):
            return None
        incomplete = _missing_setup_sections(deps)
        # ``active`` is an optional deep-link query param; when absent the
        # page falls back to its own first-incomplete-section logic.
        state = (
            settings_page.SettingsState(
                incomplete_sections=incomplete,
                active_section=active,
            )
            if active
            else settings_page.SettingsState(incomplete_sections=incomplete)
        )
        config = getattr(deps, "config", None) if deps is not None else None

        def _on_save(updated: Any) -> None:
            if not _persist_config(deps, updated, ui):
                return
            ui.navigate.to("/restart-required")

        on_save_lims_password, on_clear_lims_password = _lims_credential_handlers(deps, ui)

        # ``on_select_section`` is left unset: the settings dialog swaps
        # sections client-side, so a navigation hook would only reload
        # the page and discard the operator's in-progress edits.
        return settings_page.render_settings_page(
            config=config,
            state=state,
            on_save=_on_save,
            on_discard=None,
            on_save_lims_password=on_save_lims_password,
            on_clear_lims_password=on_clear_lims_password,
            lims_password_present=bool(getattr(deps, "keyring_password_present", False)),
        )

    @ui.page("/problems")
    def _problems() -> Any:
        deps = _deps()
        if _restart_gate(deps, ui):
            return None
        findings = _safe_audit(deps)
        return problems_page.render_problems_page(findings=findings)

    @ui.page("/staging")
    def _staging() -> Any:
        deps = _deps()
        if _restart_gate(deps, ui):
            return None
        state = _build_staging_state(deps)
        if state is None:
            _render_unavailable(
                ui,
                "Staging unavailable",
                "No config is wired on this app instance.",
            )
            return None
        return staging_page.render_staging_dock(state)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _restart_gate(deps: Any, ui: Any) -> bool:
    """Route to ``/restart-required`` when config was written this session.

    The config-dependent components (controller / lims_client /
    nas_sync) are built once at tray boot, so a config.yaml written by
    the settings wizard only takes effect after a relaunch. Returns
    ``True`` when the caller should stop rendering its normal page.
    """
    if deps is not None and getattr(deps, "restart_required", False):
        ui.navigate.to("/restart-required")
        return True
    return False


def _lims_credential_handlers(
    deps: Any, ui: Any
) -> tuple[Callable[[str], None], Callable[[], None]]:
    """Build the LIMS-password Save / Clear handlers for the settings dialog.

    Credentials are independent of the config Save (Frontend Spec §7.3):
    these write straight to the OS keyring via ``deps.keyring_store`` at
    click time, under the ``(exlab-wizard, lims)`` pair. A missing
    keyring store (best-effort construction failed at tray boot) or a
    backend error surfaces as a negative toast instead of crashing the
    page.
    """
    keyring_store = getattr(deps, "keyring_store", None) if deps is not None else None

    def _on_save(value: str) -> None:
        if keyring_store is None:
            _show_toast(
                ui, "Cannot save the password: the OS keyring is unavailable", positive=False
            )
            return
        try:
            keyring_store.set_password(username=KEYRING_USERNAME_LIMS, password=value)
        except Exception as exc:
            _log.exception("LIMS keyring set_password failed")
            _show_toast(ui, f"Could not save the LIMS password: {exc}", positive=False)
            return
        _show_toast(ui, "LIMS password saved to the OS keyring", positive=True)

    def _on_clear() -> None:
        if keyring_store is None:
            _show_toast(
                ui, "Cannot clear the password: the OS keyring is unavailable", positive=False
            )
            return
        try:
            keyring_store.delete_password(username=KEYRING_USERNAME_LIMS)
        except Exception as exc:
            _log.exception("LIMS keyring delete_password failed")
            _show_toast(ui, f"Could not clear the LIMS password: {exc}", positive=False)
            return
        _show_toast(ui, "LIMS password removed from the OS keyring", positive=True)

    return _on_save, _on_clear


def _persist_config(deps: Any, updated: Any, ui: Any) -> bool:
    """Write ``updated`` via ``deps.save_config`` and arm the restart gate.

    Returns ``True`` on success. On failure a negative toast is shown
    and the function returns ``False`` so the caller leaves the operator
    on the settings page to retry.
    """
    saver = getattr(deps, "save_config", None) if deps is not None else None
    if saver is None:
        _show_toast(ui, "Cannot save: no config writer is available", positive=False)
        return False
    try:
        result = saver(updated)
        if hasattr(result, "__await__"):
            # Production wires a synchronous saver; an awaitable here
            # would silently no-op, so surface it rather than swallow.
            _log.warning("save_config returned an awaitable; a sync saver is expected")
    except Exception as exc:
        _log.exception("save_config failed")
        _show_toast(ui, f"Save failed: {exc}", positive=False)
        return False
    if deps is not None:
        deps.config = updated
        deps.restart_required = True
    return True


def _render_restart_required(ui: Any) -> Any:
    """Render the terminal restart-required screen."""
    try:
        card = (
            ui.card()
            .props('data-testid="restart-required"')
            .style(
                "max-width: 520px; margin: 4rem auto; padding: var(--sp-8); "
                "background: var(--color-surface); border-radius: var(--radius-lg);"
            )
        )
        with card:
            ui.label("Restart required").style(
                "font-family: var(--font-display); font-size: var(--text-lg); "
                "font-weight: 600; color: var(--color-heading);"
            )
            ui.label(
                "Your configuration has been saved. Quit ExLab-Wizard from the "
                "system tray and relaunch it so the new settings take effect."
            ).props('data-testid="restart-required-message"').style("color: var(--color-body);")
        return card
    except Exception as exc:
        _log.warning("render_restart_required failed: %s", exc)
        return None


def _is_setup_ready(deps: Any) -> bool:
    """Mirror ``api.setup.compute_setup_state`` without the API import."""
    if deps is None or getattr(deps, "config", None) is None:
        return False
    keyring = getattr(deps, "keyring_password_present", False)
    lims_reachable = getattr(deps, "lims_reachable", True)
    return bool(keyring and lims_reachable)


def _apply_autostart(deps: Any, enabled: bool) -> None:
    if deps is None:
        return
    toggle: Callable[[bool], Any] | None = getattr(deps, "autostart_toggle", None)
    if toggle is None:
        return
    try:
        toggle(enabled)
    except Exception as exc:
        _log.warning("autostart toggle failed in welcome: %s", exc)


def _build_main_state(deps: Any) -> Any:
    from exlab_wizard.ui.pages import main as main_page

    # Redesign §3.1: orchestrator pipeline is always active; the staging
    # surface always renders, so MainPageState.orchestrator_enabled keeps
    # its True default.
    return main_page.MainPageState(setup_incomplete=not _is_setup_ready(deps))


def _missing_setup_sections(deps: Any) -> tuple[str, ...]:
    """Return the settings sections the operator still needs to fill in.

    Mirrors a subset of the §4.9 setup-state evaluation: any setup-state
    other than READY surfaces at least one section. The Settings page
    uses this to auto-select the first incomplete section.
    """
    if deps is None:
        return ("paths", "lims")
    config = getattr(deps, "config", None)
    if config is None:
        # ``operators`` is intentionally omitted: the chip editor is
        # deferred, and surfacing it here forced the operator into a
        # placeholder section before they could reach the main GUI.
        return ("paths", "lims")
    missing: list[str] = []
    if not config.paths.local_root or not config.paths.templates_dir:
        missing.append("paths")
    if not config.lims.endpoint or not config.lims.email:
        missing.append("lims")
    if not getattr(deps, "keyring_password_present", False) and "lims" not in missing:
        missing.append("lims")
    return tuple(missing)


def _templates_dir(deps: Any) -> Path | None:
    """Return the configured templates directory, or ``None``."""
    config = getattr(deps, "config", None) if deps is not None else None
    if config is None or not config.paths.templates_dir:
        return None
    return Path(config.paths.templates_dir)


def _template_names(deps: Any, template_type: str) -> list[str]:
    """List template directory names of ``template_type`` under templates_dir."""
    templates_dir = _templates_dir(deps)
    if templates_dir is None:
        return []
    try:
        from exlab_wizard.ui.pages import templates as templates_page

        return [
            summary.name
            for summary in templates_page.list_templates(templates_dir, template_type=template_type)
        ]
    except Exception as exc:
        _log.warning("template scan failed: %s", exc)
        return []


def _template_questions_map(deps: Any, template_type: str) -> dict[str, Any]:
    """Map each ``template_type`` template name to its parsed copier questions.

    Resolves every template through the real ``TemplateEngine`` so the
    wizard's dynamic Variables step is driven by the actual
    ``copier.yml`` question definitions. A template that fails to
    resolve is skipped with a WARN -- its wizard entry simply shows no
    variables.
    """
    templates_dir = _templates_dir(deps)
    if templates_dir is None:
        return {}
    try:
        from exlab_wizard.constants import TemplateType
        from exlab_wizard.template.copier_driver import TemplateEngine
        from exlab_wizard.ui.pages import templates as templates_page

        engine = TemplateEngine()
        scope = TemplateType(template_type)
        result: dict[str, Any] = {}
        for summary in templates_page.list_templates(templates_dir, template_type=template_type):
            try:
                resolved = engine.resolve(summary.path, scope)
            except Exception as exc:
                _log.warning("template %s failed to resolve: %s", summary.name, exc)
                continue
            result[summary.name] = templates_page.template_questions(resolved.raw_manifest)
        return result
    except Exception as exc:
        _log.warning("template question scan failed: %s", exc)
        return {}


def _lims_catalogue_projects(deps: Any) -> list[dict[str, Any]]:
    """Read the offline-catalogue projects (disconnected-workstation source).

    Reads the offline catalogue (``config.lims.offline_catalogue_path``).
    Returns ``[]`` on any failure -- missing path, schema mismatch, parse
    error -- so callers can fall through to the next picker source.
    """
    config = getattr(deps, "config", None) if deps is not None else None
    if config is None:
        return []
    catalogue_path = getattr(config.lims, "offline_catalogue_path", "") or ""
    if not catalogue_path or not Path(catalogue_path).exists():
        return []
    try:
        from exlab_wizard.lims.catalogue import read_catalogue

        catalogue = read_catalogue(Path(catalogue_path), expected_endpoint=config.lims.endpoint)
        return [
            {
                "short_id": project.short_id,
                "name": project.name,
                "uid": project.uid,
                "source": "offline_catalogue",
            }
            for project in catalogue.projects
        ]
    except Exception as exc:
        _log.warning("offline catalogue read failed: %s", exc)
        return []


async def _lims_projects(deps: Any) -> list[dict[str, Any]]:
    """Return the LIMS projects backing the project wizard's picker.

    Tries the live LIMS first (``deps.lims_client.list_projects``); on any
    failure -- client absent, unreachable, auth, timeout -- falls back to
    the offline catalogue (``config.lims.offline_catalogue_path``). Returns
    ``[]`` when neither source yields rows, in which case the wizard offers
    a deliberate manual-entry gate instead of a dropdown.
    """
    lims_client = getattr(deps, "lims_client", None) if deps is not None else None
    lims_reachable = getattr(deps, "lims_reachable", True) if deps is not None else False
    if lims_client is not None and lims_reachable:
        try:
            projects = await asyncio.wait_for(lims_client.list_projects(), timeout=5.0)
            rows = [
                {
                    "short_id": project.short_id,
                    "name": project.name,
                    "uid": project.uid,
                    "source": "lims",
                }
                for project in projects
            ]
            if rows:
                return rows
        except Exception as exc:
            _log.warning("live LIMS project list failed: %s", exc)
    return _lims_catalogue_projects(deps)


def _equipment_ids(deps: Any) -> list[str]:
    """Return the configured equipment IDs."""
    config = getattr(deps, "config", None) if deps is not None else None
    if config is None:
        return []
    return [entry.id for entry in config.equipment]


async def _await_session(controller: Any, handle: Any) -> Any:
    """Await a controller session's pipeline task and return the final handle.

    ``create_project`` / ``create_run`` return immediately with the
    post-validation handle and run the rest of the pipeline as a
    background task tracked in ``controller._tasks`` (the integration
    suite drains it the same way). We await that task so the wizard
    shows a real DONE / FAILED outcome rather than the transient
    RENDERING state.
    """
    task = controller._tasks.get(handle.session_id)
    if task is not None:
        with contextlib.suppress(Exception):
            await task
    return await controller.status(handle.session_id)


async def _submit_project(deps: Any, state: Any, ui: Any) -> None:
    """Build a ProjectCreateRequest from the wizard state and run it."""
    controller = getattr(deps, "controller", None) if deps is not None else None
    if controller is None:
        _show_toast(ui, "Project creation unavailable: controller not initialized", positive=False)
        return
    templates_dir = _templates_dir(deps)
    if templates_dir is None or not state.selected_template:
        _show_toast(ui, "Pick a template before creating the project", positive=False)
        return

    from exlab_wizard.controller.creation import ProjectCreateRequest

    readme = state.readme_fields
    request = ProjectCreateRequest(
        equipment_id=state.selected_equipment or "",
        template_path=templates_dir / state.selected_template,
        lims_project={
            "uid": str(uuid.uuid4()),
            "short_id": state.selected_lims_short_id or "",
            "name_at_creation": state.lims_project_name or "",
            "source": getattr(state, "selected_lims_source", "manual") or "manual",
        },
        variables=dict(state.template_variables),
        label=readme.get("label", ""),
        operator=readme.get("operator", ""),
        objective=readme.get("objective", ""),
    )
    await _run_creation(controller, controller.create_project, request, ui, label="Project")


async def _submit_run(deps: Any, state: Any, run_kind: RunKind, ui: Any) -> None:
    """Build a RunCreateRequest from the wizard state and run it."""
    controller = getattr(deps, "controller", None) if deps is not None else None
    if controller is None:
        _show_toast(ui, "Run creation unavailable: controller not initialized", positive=False)
        return
    templates_dir = _templates_dir(deps)
    if templates_dir is None or not state.selected_template:
        _show_toast(ui, "Pick a template before creating the run", positive=False)
        return

    from exlab_wizard.controller.creation import RunCreateRequest

    readme = state.readme_fields
    # The run lives under <equipment>/<project name>/ (Backend Spec §3.2);
    # the controller inherits the parent project's full LIMS identity
    # (uid / short_id / source) from that project's creation.json.
    request = RunCreateRequest(
        equipment_id=state.selected_equipment or "",
        project_name=state.selected_project_name or "",
        template_path=templates_dir / state.selected_template,
        run_kind=run_kind,
        variables=dict(state.template_variables),
        label=readme.get("label", ""),
        operator=readme.get("operator", ""),
        objective=readme.get("objective", ""),
    )
    kind_label = "Test run" if run_kind is RunKind.TEST else "Run"
    await _run_creation(controller, controller.create_run, request, ui, label=kind_label)


async def _run_creation(
    controller: Any,
    create_fn: Callable[[Any], Any],
    request: Any,
    ui: Any,
    *,
    label: str,
) -> None:
    """Drive a create_* call to completion and toast the outcome."""
    from exlab_wizard.controller import SessionState

    try:
        handle = await create_fn(request)
        final = await _await_session(controller, handle)
    except Exception as exc:
        _log.exception("%s creation raised", label)
        _show_toast(ui, f"{label} creation failed: {exc}", positive=False)
        return
    if final.state is SessionState.DONE:
        _show_toast(ui, f"{label} created", positive=True)
        ui.navigate.to("/main")
        return
    detail = ""
    session = controller.session_store.get(handle.session_id)
    if session is not None and session.error:
        detail = f": {session.error.get('message', session.error.get('code', ''))}"
    _show_toast(ui, f"{label} creation {final.state.value}{detail}", positive=False)


def _render_run_wizard(deps: Any, run_kind: RunKind, ui: Any) -> Any:
    from exlab_wizard.ui.pages import wizard_run as wizard_run_page

    state = wizard_run_page.RunWizardState(run_kind=run_kind)
    return wizard_run_page.render_run_wizard(
        state=state,
        templates=_template_names(deps, "run"),
        equipment_ids=_equipment_ids(deps),
        template_questions=_template_questions_map(deps, "run"),
        on_submit=lambda submitted: _submit_run(deps, submitted, run_kind, ui),
        on_cancel=lambda: ui.navigate.to("/main"),
    )


def _safe_audit(deps: Any) -> list[Any]:
    """Run the validator audit, swallowing failures to a WARN log."""
    validator = getattr(deps, "validator", None) if deps is not None else None
    if validator is None:
        return []
    try:
        return list(validator.audit({"kind": AuditScopeKind.ALL}))
    except Exception as exc:
        _log.warning("validator.audit failed: %s", exc)
        return []


def _build_staging_state(deps: Any) -> Any:
    from exlab_wizard.ui.pages import staging as staging_page

    config = getattr(deps, "config", None) if deps is not None else None
    if config is None:
        return None
    # Redesign §3.1: orchestrator pipeline is always active; missing
    # staging_root surfaces as an empty staging dock, not a None panel.
    try:
        from exlab_wizard.orchestrator.staging_query import list_staged_runs

        rows = list_staged_runs(config=config)
    except Exception as exc:
        _log.warning("staging_query failed: %s", exc)
        return staging_page.StagingDockState(rows=[])
    return staging_page.StagingDockState(rows=list(rows))


def _show_toast(ui: Any, message: str, *, positive: bool) -> None:
    del ui  # toasts route through the notifications helper, not raw ui
    try:
        from exlab_wizard.ui import notifications

        if positive:
            notifications.notify_success(message)
        else:
            notifications.notify_error(message)
    except Exception as exc:
        _log.debug("toast notify failed: %s", exc)


def _render_unavailable(ui: Any, headline: str, subline: str) -> None:
    try:
        with ui.card().style("max-width: 480px; padding: var(--sp-6);"):
            ui.label(headline).style("font-weight: 600;")
            ui.label(subline).style("color: var(--color-muted);")
    except Exception as exc:
        _log.warning("render_unavailable failed: %s", exc)
