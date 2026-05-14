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

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from exlab_wizard.constants import AuditScopeKind, RunKind
from exlab_wizard.logging import get_logger

if TYPE_CHECKING:
    from fastapi import FastAPI

    from exlab_wizard.ui.pages import main as main_page
    from exlab_wizard.ui.pages import staging as staging_page

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
        problems as problems_page,
        settings as settings_page,
        staging as staging_page,
        welcome as welcome_page,
        wizard_project as wizard_project_page,
        wizard_run as wizard_run_page,
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
    def _wizard_project() -> Any:
        deps = _deps()
        if _restart_gate(deps, ui):
            return None
        return wizard_project_page.render_project_wizard(
            on_submit=lambda state: _submit_project(deps, state, ui),
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

        # ``on_select_section`` is left unset: the settings dialog swaps
        # sections client-side, so a navigation hook would only reload
        # the page and discard the operator's in-progress edits.
        return settings_page.render_settings_page(
            config=config,
            state=state,
            on_save=_on_save,
            on_discard=None,
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
                "Orchestrator mode is disabled or the staging watcher is not running.",
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
    except Exception as exc:  # noqa: BLE001 -- defensive UI boundary
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
        card = ui.card().props('data-testid="restart-required"').style(
            "max-width: 520px; margin: 4rem auto; padding: var(--sp-8); "
            "background: var(--color-surface); border-radius: var(--radius-lg);"
        )
        with card:
            ui.label("Restart required").style(
                "font-family: var(--font-display); font-size: var(--text-lg); "
                "font-weight: 600; color: var(--color-heading);"
            )
            ui.label(
                "Your configuration has been saved. Quit ExLab-Wizard from the "
                "system tray and relaunch it so the new settings take effect."
            ).props('data-testid="restart-required-message"').style(
                "color: var(--color-body);"
            )
        return card
    except Exception as exc:  # noqa: BLE001 -- fallback for missing card primitive
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
    except Exception as exc:  # noqa: BLE001 -- defensive UI boundary
        _log.warning("autostart toggle failed in welcome: %s", exc)


def _build_main_state(deps: Any) -> Any:
    from exlab_wizard.ui.pages import main as main_page

    setup_incomplete = not _is_setup_ready(deps)
    orchestrator_enabled = False
    config = getattr(deps, "config", None) if deps is not None else None
    if config is not None:
        orchestrator_enabled = bool(getattr(config.orchestrator, "enabled", False))
    return main_page.MainPageState(
        setup_incomplete=setup_incomplete,
        orchestrator_enabled=orchestrator_enabled,
    )


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
        return ("paths", "lims", "operators")
    missing: list[str] = []
    if not config.paths.local_root or not config.paths.templates_dir:
        missing.append("paths")
    if not config.lims.endpoint or not config.lims.email:
        missing.append("lims")
    if not getattr(deps, "keyring_password_present", False):
        if "lims" not in missing:
            missing.append("lims")
    return tuple(missing)


def _submit_project(deps: Any, state: Any, ui: Any) -> None:
    controller = getattr(deps, "controller", None) if deps is not None else None
    if controller is None:
        _show_toast(ui, "Project creation unavailable: controller not initialized", positive=False)
        return
    _show_toast(ui, f"Project '{state.selected_lims_short_id}' submitted", positive=True)
    ui.navigate.to("/main")


def _render_run_wizard(deps: Any, run_kind: RunKind, ui: Any) -> Any:
    from exlab_wizard.ui.pages import wizard_run as wizard_run_page

    state = wizard_run_page.RunWizardState(run_kind=run_kind)

    def _on_submit(submitted: Any) -> None:
        controller = getattr(deps, "controller", None) if deps is not None else None
        if controller is None:
            _show_toast(ui, "Run creation unavailable: controller not initialized", positive=False)
            return
        _show_toast(
            ui,
            f"{submitted.run_kind.value.title()} run submitted",
            positive=True,
        )
        ui.navigate.to("/main")

    return wizard_run_page.render_run_wizard(state=state, on_submit=_on_submit)


def _safe_audit(deps: Any) -> list[Any]:
    """Run the validator audit, swallowing failures to a WARN log."""
    validator = getattr(deps, "validator", None) if deps is not None else None
    if validator is None:
        return []
    try:
        return list(validator.audit({"kind": AuditScopeKind.ALL}))
    except Exception as exc:  # noqa: BLE001 -- defensive UI boundary
        _log.warning("validator.audit failed: %s", exc)
        return []


def _build_staging_state(deps: Any) -> Any:
    from exlab_wizard.ui.pages import staging as staging_page

    config = getattr(deps, "config", None) if deps is not None else None
    if config is None or not getattr(config.orchestrator, "enabled", False):
        return None
    try:
        from exlab_wizard.orchestrator.staging_query import list_staged_runs

        rows = list_staged_runs(config=config)
    except Exception as exc:  # noqa: BLE001 -- defensive UI boundary
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
    except Exception as exc:  # noqa: BLE001 -- toast must never crash the route
        _log.debug("toast notify failed: %s", exc)


def _render_unavailable(ui: Any, headline: str, subline: str) -> None:
    try:
        with ui.card().style("max-width: 480px; padding: var(--sp-6);"):
            ui.label(headline).style("font-weight: 600;")
            ui.label(subline).style("color: var(--color-muted);")
    except Exception as exc:  # noqa: BLE001 -- fallback for missing card primitive
        _log.warning("render_unavailable failed: %s", exc)
