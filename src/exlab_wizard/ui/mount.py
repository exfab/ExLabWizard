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
import os
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from exlab_wizard.constants import (
    KEYRING_USERNAME_LIMS,
    AuditScopeKind,
    RunKind,
    SetupState,
)
from exlab_wizard.logging import get_logger

# clear_run_dir backs the per-run tree context-menu "clear" action (force-sync /
# clear / view-log), which applies to nas-mode runs too — kept after the staging
# dock was hidden. See
# docs/superpowers/specs/2026-05-29-hide-orchestrator-staging-design.md.
from exlab_wizard.orchestrator.staging_clear import clear_run_dir

if TYPE_CHECKING:
    from fastapi import FastAPI

    from exlab_wizard.template.resolution import TemplateChoices


__all__ = ["MOUNT_PATH", "mount_ui"]

_log = get_logger(__name__)

MOUNT_PATH = "/"

# Strong references for the fire-and-forget asyncio tasks the mount
# spawns (force-sync, clear, bulk clear-verified). asyncio is documented
# to drop tasks whose only reference is the event loop, so we hold them
# in a module-level set until they finish to prevent unexpected
# cancellation under load.
_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()


def _spawn_background(coro: Any) -> asyncio.Task[Any]:
    """Schedule ``coro`` and keep a strong reference until it finishes."""
    task = asyncio.create_task(coro)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return task


def mount_ui(app: FastAPI, *, storage_secret: str) -> None:
    """Register every wizard page on ``app`` and mount NiceGUI at ``/``.

    ``storage_secret`` is the per-installation token from
    :mod:`exlab_wizard.tray.storage_secret`; NiceGUI uses it to sign the
    Starlette ``SessionMiddleware`` cookie that backs
    ``app.storage.user``. The codebase doesn't read ``app.storage.*``
    today but NiceGUI refuses to mount without a non-empty value.
    """
    from nicegui import ui

    from exlab_wizard.ui.theme import register_static_assets, register_theme

    register_static_assets()
    # Inject the canonical :root design-token block app-wide (shared=True) so
    # every page's components resolve var(--color-*)/var(--sp-*)/... to real
    # values rather than falling back to scattered literals (the block was
    # previously never wired into the running app).
    register_theme()
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
        template_editor as template_editor_page,
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
        if _is_setup_ready(deps):
            ui.navigate.to("/main")
        else:
            ui.navigate.to("/welcome")

    @ui.page("/welcome")
    def _welcome() -> Any:
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
    def _main(
        selected: str = "",
        right_pane: str = "",
        file: str = "",
        q: str = "",
        density: str = "",
    ) -> Any:
        deps = _deps()
        from exlab_wizard.api.routers import browse as _browse

        config = getattr(deps, "config", None)
        hierarchy = _browse.build_hierarchy_dict(config)
        selected_path = selected or None
        node_kind, is_received = _classify_node(selected_path, hierarchy)
        right_pane_collapsed = right_pane == "collapsed"
        # Kick off / rebind the folder feed for the selected path and
        # gather the most recent payload (empty list on first render or if
        # the feed hasn't ticked yet). Resolved before the state build so a
        # ?file= selection can be matched against the in-memory feed.
        feed_entries = _drive_folder_feed(app, deps, selected_path)
        selected_file = _build_selected_file(file or None, feed_entries, deps)
        state = _build_main_state(
            deps,
            selected_node=selected_path,
            node_kind=node_kind,
            is_received=is_received,
            right_pane_collapsed=right_pane_collapsed,
            selected_file_path=file or None,
            selected_file=selected_file,
            search_query=q,
            density=density,
        )
        metadata_payload = _build_metadata_payload(selected_path, node_kind, deps)

        def _refresh() -> None:
            ui.navigate.to(
                "/main" + _build_main_query(selected, right_pane, file=file, q=q, density=density)
            )

        def _on_select_node(node_id: str) -> None:
            # Selecting a new tree node repaints the centre list for the new
            # folder, so any ?file= selection (a path under the *previous*
            # folder) is dropped; search query + density carry over.
            ui.navigate.to("/main" + _build_main_query(node_id, right_pane, q=q, density=density))

        def _on_toggle_right_pane() -> None:
            new_pane = "" if right_pane == "collapsed" else "collapsed"
            ui.navigate.to(
                "/main" + _build_main_query(selected, new_pane, file=file, q=q, density=density)
            )

        def _on_select_file(entry: Any) -> None:
            # Single-click on a file/folder row -> carry its path on ?file=.
            ui.navigate.to(
                "/main"
                + _build_main_query(
                    selected, right_pane, file=getattr(entry, "path", ""), q=q, density=density
                )
            )

        def _on_refresh_folder() -> None:
            # Files-pane refresh: re-scan the open folder now, then re-render.
            _refresh_selected_folder(app, deps, selected_path)
            ui.navigate.to(
                "/main" + _build_main_query(selected, right_pane, file=file, q=q, density=density)
            )

        def _on_search(query: str) -> None:
            # Typing in the search box re-filters the tree only; keep the
            # selected node, file selection, pane state and density and swap
            # just ?q= (debounced render-side so this fires once per pause).
            ui.navigate.to(
                "/main"
                + _build_main_query(selected, right_pane, file=file, q=query, density=density)
            )

        def _on_toggle_density() -> None:
            # Files-pane row density (§4.8): flip compact <-> comfortable,
            # keep everything else, swap only ?density=.
            new_density = "" if density == "compact" else "compact"
            ui.navigate.to(
                "/main"
                + _build_main_query(selected, right_pane, file=file, q=q, density=new_density)
            )

        def _on_run_staging_action(path: str, action: str) -> None:
            _run_staging_action(deps, path, action, ui)

        def _on_tree_context_action(node_id: str, action: str) -> None:
            # Either edit or remove deep-links into Settings with the
            # equipment pre-selected (Redesign §4.6 / decision 4A).
            del action  # both actions route to the same destination today
            ui.navigate.to(f"/settings?active=equipment&equipment_id={node_id}")

        def _on_file_context_action(entry: Any, action: str) -> None:
            _file_context_action(deps, entry, action, ui, on_done=_refresh)

        return main_page.render_file_explorer_page(
            on_open_new_project=lambda: ui.navigate.to("/wizard/project"),
            on_open_new_run=lambda: ui.navigate.to("/wizard/run"),
            on_open_new_test_run=lambda: ui.navigate.to("/wizard/test-run"),
            on_open_add_equipment=lambda: ui.navigate.to("/wizard/equipment"),
            on_open_settings=lambda: ui.navigate.to("/settings"),
            on_refresh=_refresh,
            on_select_node=_on_select_node,
            on_open_operations=lambda: _open_operations_modal(deps, ui),
            on_navigate_breadcrumb=_on_select_node,
            on_toggle_right_pane=_on_toggle_right_pane,
            on_run_staging_action=_on_run_staging_action,
            on_tree_context_action=_on_tree_context_action,
            on_file_context_action=_on_file_context_action,
            on_select_file=_on_select_file,
            on_refresh_folder=_on_refresh_folder,
            on_search=_on_search,
            on_toggle_density=_on_toggle_density,
            state=state,
            hierarchy=hierarchy,
            file_list_entries=feed_entries,
            metadata_payload=metadata_payload,
        )

    @ui.page("/wizard/project")
    async def _wizard_project() -> Any:
        deps = _deps()
        initial = _resolve_template_choices(deps, "project")

        def _resolve_project_templates(equipment_id: str | None) -> Any:
            # Per-equipment project templates layer over the global store
            # once the operator picks equipment (step 3, after the template
            # step -- so this reflects on a step-back). Backend Spec §5.0.
            return _resolve_template_choices(deps, "project", equipment_id=equipment_id)

        return wizard_project_page.render_project_wizard(
            templates=initial.names,
            equipment_ids=_equipment_ids(deps),
            template_questions=initial.questions,
            template_paths=initial.paths,
            on_resolve=_resolve_project_templates,
            lims_projects=await _lims_projects(deps),
            on_submit=lambda state: _submit_project(deps, state, ui),
            on_cancel=lambda: ui.navigate.to("/main"),
        )

    @ui.page("/wizard/run")
    def _wizard_run() -> Any:
        deps = _deps()
        return _render_run_wizard(deps, RunKind.EXPERIMENTAL, ui)

    @ui.page("/wizard/test-run")
    def _wizard_test_run() -> Any:
        deps = _deps()
        return _render_run_wizard(deps, RunKind.TEST, ui)

    @ui.page("/wizard/equipment")
    def _wizard_equipment() -> Any:
        """Redesign §6 — Add-Equipment wizard route.

        ``state`` is created once for the wizard's whole lifetime: the
        render layer drives Next / Back internally (re-rendering in
        place), so nothing here navigates mid-wizard -- a navigation
        would rebuild the page and reset every field the operator typed.
        """
        deps = _deps()
        state = wizard_equipment_page.EquipmentWizardState()

        def _on_confirm(eq: Any) -> None:
            # Persist straight into the live config (Redesign §6): merge
            # via the same shared helper the POST /config/equipment route
            # uses, save through ``deps.save_config``, then push the merged
            # config into the running NAS-sync client / poller via
            # ``apply_live_config`` so the new equipment is live without a
            # tray relaunch -- matching the route's no-restart contract.
            from exlab_wizard.config.models import config_with_equipment_appended
            from exlab_wizard.errors import ConfigError

            try:
                merged = config_with_equipment_appended(getattr(deps, "config", None), eq)
            except ConfigError as exc:
                _show_toast(ui, f"Could not add equipment: {exc}", positive=False)
                return
            saver = getattr(deps, "save_config", None) if deps is not None else None
            if saver is None:
                _show_toast(
                    ui, "Cannot add equipment: no config writer is available", positive=False
                )
                return
            try:
                result = saver(merged)
                if hasattr(result, "__await__"):
                    # Production wires a synchronous saver; an awaitable
                    # here would silently no-op, so surface it.
                    _log.warning("save_config returned an awaitable; a sync saver is expected")
            except Exception as exc:
                _log.exception("append-equipment save_config failed")
                _show_toast(ui, f"Could not add equipment: {exc}", positive=False)
                return
            if deps is not None:
                _apply_live_config(deps, merged)
            ui.navigate.to("/main")

        return wizard_equipment_page.render_wizard_equipment(
            state=state,
            on_confirm=_on_confirm,
            on_cancel=lambda: ui.navigate.to("/main"),
        )

    @ui.page("/templates")
    def _templates(loc: str = "global") -> Any:
        deps = _deps()
        # Global scaffolding always targets the flat templates_dir; the
        # location selector only changes which directories are *listed*.
        templates_dir = _templates_dir(deps)
        scan_dirs = _location_scan_dirs(deps, loc)

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

        # Merge the listings across the location's scan dirs (the global
        # location has one flat dir; an equipment location has its
        # type-segregated project/ + run/ stores). ``rel_by_name`` records
        # the on-disk path so the Edit link can deep-link straight to it.
        summaries: list[Any] = []
        dir_by_name: dict[str, Path] = {}
        for directory in scan_dirs:
            for summary in templates_page.list_templates(directory):
                if summary.name in dir_by_name:
                    continue
                dir_by_name[summary.name] = summary.path
                summaries.append(summary)

        def _on_edit(name: str) -> None:
            target = dir_by_name.get(name)
            if target is None:
                _show_toast(ui, f"Template {name!r} not found", positive=False)
                return
            ui.navigate.to(f"/templates/edit?dir={target}")

        return templates_page.render_template_manager(
            templates=summaries,
            on_create=_on_create,
            on_back=lambda: ui.navigate.to("/main"),
            on_edit=_on_edit,
            locations=_template_locations(deps),
            on_location_change=lambda value: ui.navigate.to(f"/templates?loc={value}"),
        )

    @ui.page("/templates/edit")
    def _templates_edit(dir: str = "") -> Any:  # query-param name (binds NiceGUI ?dir=)
        from exlab_wizard.template import authoring

        template_dir = Path(dir) if dir else None
        if template_dir is None or not template_dir.is_dir():
            _show_toast(ui, "Template not found; returning to templates", positive=False)
            ui.navigate.to("/templates")
            return None

        def _reload() -> None:
            ui.navigate.to(f"/templates/edit?dir={template_dir}")

        def _on_save_manifest(manifest: Any) -> None:
            try:
                _, expected = authoring.read_manifest(template_dir)
                authoring.write_manifest(template_dir, manifest, expected_stat=expected)
            except (
                authoring.StaleEditError,
                authoring.UnsafePathError,
                authoring.TemplateAuthoringError,
            ) as exc:
                _show_toast(ui, f"Questions not saved: {exc}", positive=False)
                return
            _show_toast(ui, "Questions saved", positive=True)
            _reload()

        def _on_save_content(rel: str, text: str) -> None:
            try:
                existing = template_dir / rel
                expected = None
                if existing.is_file():
                    _, expected = authoring.read_content(existing)
                authoring.write_content_file(template_dir, rel, text, expected_stat=expected)
            except (
                authoring.StaleEditError,
                authoring.UnsafePathError,
                authoring.TemplateAuthoringError,
            ) as exc:
                _show_toast(ui, f"File not saved: {exc}", positive=False)
                return
            _show_toast(ui, f"Saved {rel}", positive=True)
            _reload()

        def _on_upload(filename: str, data: bytes, render_as_template: bool) -> None:
            try:
                authoring.upload_file(
                    template_dir, filename, data, render_as_template=render_as_template
                )
            except (authoring.UnsafePathError, authoring.TemplateAuthoringError) as exc:
                _show_toast(ui, f"Upload failed: {exc}", positive=False)
                return
            _show_toast(ui, f"Uploaded {filename}", positive=True)
            _reload()

        def _on_delete(rel: str) -> None:
            try:
                authoring.delete_path(template_dir, rel)
            except (authoring.UnsafePathError, authoring.TemplateAuthoringError) as exc:
                _show_toast(ui, f"Delete failed: {exc}", positive=False)
                return
            _show_toast(ui, f"Deleted {rel}", positive=True)
            _reload()

        return template_editor_page.render_template_editor(
            template_dir=template_dir,
            on_save_manifest=_on_save_manifest,
            on_save_content=_on_save_content,
            on_upload=_on_upload,
            on_delete=_on_delete,
            on_back=lambda: ui.navigate.to("/templates"),
        )

    @ui.page("/settings")
    def _settings(active: str = "") -> Any:
        from exlab_wizard.api._dependencies import lims_password_present, nas_remote_available

        deps = _deps()
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
            # Live-applied in-process (no relaunch): confirm and keep the
            # operator on the settings page so they can keep editing.
            _show_toast(ui, "Settings saved", positive=True)

        on_save_lims_password, on_clear_lims_password = _lims_credential_handlers(deps, ui)

        def _on_set_autostart(enabled: bool) -> bool | None:
            return _apply_autostart(deps, enabled)

        # Quit hook (T9): run the graceful-shutdown hook on a separate thread,
        # NOT via ui.timer. The timer callback runs on the server's *running*
        # event loop, where ``request_quit``'s ``asyncio.run(...)`` raises
        # "loop already running" (and the fallback re-raises) -- the app would
        # never shut down. A fresh thread has no running loop so ``asyncio.run``
        # works; the click handler returns immediately so the HTTP response
        # still flushes. Absent in headless/test fixtures.
        _quit_hook = getattr(deps, "request_quit", None) if deps is not None else None
        on_quit: Callable[[], None] | None = None
        if _quit_hook is not None:
            quit_hook = _quit_hook

            def on_quit() -> None:
                import threading

                def _do() -> None:
                    try:
                        quit_hook()
                    except Exception as exc:
                        _log.warning("quit hook raised: %s", exc)

                threading.Thread(target=_do, name="exlab-quit", daemon=True).start()

        async def _on_test_connection() -> Any:
            return await _nas_test_connection(deps)

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
            lims_password_present=lims_password_present(deps),
            nas_remote_available=lambda remote: nas_remote_available(deps, remote),
            on_test_connection=_on_test_connection,
            autostart_registered=bool(getattr(deps, "autostart_is_registered", False)),
            on_set_autostart=_on_set_autostart,
            on_quit=on_quit,
            tray_available=bool(getattr(deps, "tray_available", False)),
        )

    @ui.page("/problems")
    def _problems() -> Any:
        deps = _deps()
        findings = _safe_audit(deps)
        return problems_page.render_problems_page(
            findings=findings,
            last_audit_at=getattr(deps, "last_audit_at", None),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
        # The credential field re-seeds its "Set / Not set" status from
        # ``deps.keyring_password_present`` on the next render, and the
        # §4.9 setup gate reads the same flag. It is computed once at
        # tray boot, so flip it here -- otherwise a freshly saved
        # password would still read as absent until the next tray launch.
        if deps is not None:
            deps.keyring_password_present = True
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
        # Mirror of the Save path: clearing the password makes the slot
        # incomplete again, so drop the boot-time flag in step.
        if deps is not None:
            deps.keyring_password_present = False
        _show_toast(ui, "LIMS password removed from the OS keyring", positive=True)

    return _on_save, _on_clear


async def _nas_test_connection(deps: Any) -> Any:
    """Run the rclone NAS-remote probe and adapt it for the inline panel.

    rclone.conf NAS-sync migration. The Settings "NAS Remote" section's
    Test-connection button probes the single configured ``nas:`` remote
    (no per-equipment password). It reuses ``deps.equipment_probe`` -- the
    same probe the ``POST /setup/test-equipment`` endpoint uses, which now
    targets ``nas.remote`` and ignores the per-equipment fields -- passing
    the first nas-mode equipment (or any equipment) as the probe argument.
    The probe's ``{ok, reason, latency_ms}`` dict is mapped to a
    :class:`TestConnectionResult`.
    """
    import json

    from exlab_wizard.constants import SyncMode
    from exlab_wizard.ui.components.test_connection_panel import TestConnectionResult

    config = getattr(deps, "config", None) if deps is not None else None
    probe = getattr(deps, "equipment_probe", None) if deps is not None else None
    if probe is None or config is None:
        return TestConnectionResult(
            success=False,
            headline="Connection failed",
            detail="equipment probe is not available",
            raw="",
        )
    equipment = next(
        (e for e in config.equipment if e.sync_mode == SyncMode.NAS),
        next(iter(config.equipment), None),
    )
    try:
        result = probe(equipment)
        if asyncio.iscoroutine(result) or asyncio.isfuture(result):
            result = await result
    except Exception as exc:
        return TestConnectionResult(
            success=False, headline="Connection failed", detail=str(exc), raw=str(exc)
        )
    payload = result if isinstance(result, dict) else {"ok": bool(result)}
    ok = bool(payload.get("ok"))
    reason = payload.get("reason")
    latency_ms = payload.get("latency_ms")
    if ok:
        detail = f"reachable ({latency_ms} ms)" if latency_ms is not None else "reachable"
        headline = "Connected"
    else:
        detail = str(reason) if reason else "connection failed"
        headline = "Connection failed"
    return TestConnectionResult(
        success=ok,
        headline=headline,
        detail=detail,
        raw=json.dumps(payload, indent=2, sort_keys=True),
    )


def _persist_config(deps: Any, updated: Any, ui: Any) -> bool:
    """Write ``updated`` via ``deps.save_config`` and hot-reload components.

    Returns ``True`` on success. On a write failure a negative toast is
    shown and the function returns ``False`` so the caller leaves the
    operator on the settings page to retry. The disk write is the success
    boundary: once it lands, the new config is pushed into the running
    components via ``apply_live_config`` so the change takes effect
    without a tray relaunch. A hiccup in that live push is logged but
    does not fail the save -- the config is already persisted and the
    in-memory copy is kept in step.
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
    _ensure_staging_root(updated, ui)
    if deps is not None:
        _apply_live_config(deps, updated)
    return True


def _ensure_staging_root(updated: Any, ui: Any) -> None:
    """Create the staging directory when the operator saved a non-empty path.

    ``staging_root`` is opt-in: a blank value means this device is not a
    staging PC, so nothing is created. A non-empty path is created here --
    the operator specifying and saving it is the only trigger, so no
    ``/staging`` (or any staging directory) is ever made implicitly. A
    creation failure is non-fatal: the config is already persisted and the
    validator flags an inaccessible root on the next audit, so we only warn.

    ``updated`` is read defensively so a non-``Config`` value (e.g. a test
    sentinel) is a no-op rather than an ``AttributeError``.
    """
    orch = getattr(updated, "orchestrator", None)
    staging = getattr(orch, "staging_root", "")
    if not staging:
        return
    from exlab_wizard.paths import ensure_dir

    try:
        ensure_dir(Path(staging))
    except OSError as exc:
        _log.exception("failed to create staging_root")
        _show_toast(ui, f"Couldn't create staging directory: {exc}", positive=False)


def _apply_live_config(deps: Any, updated: Any) -> None:
    """Push ``updated`` into the running components (no tray relaunch).

    Wraps :func:`exlab_wizard.tray.dependencies.apply_live_config` (imported
    lazily to avoid a tray<->api import cycle). The coordinator is already
    best-effort per component, so reaching the ``except`` is unexpected;
    it still keeps ``deps.config`` in step so ``GET /config`` is correct.
    """
    try:
        from exlab_wizard.tray.dependencies import apply_live_config

        apply_live_config(deps, updated)
    except Exception:
        _log.exception("live config reload failed after save")
        deps.config = updated


def _nas_remote_missing(deps: Any, config: Any) -> bool:
    """True when nas-mode equipment exist but the ``nas:`` remote is unusable.

    rclone.conf NAS-sync migration. Drives the Settings page's NAS-remote
    section visibility via :func:`_missing_setup_sections` so the
    setup-incomplete banner auto-selects it. The remote is "missing" when
    at least one device syncs directly to the NAS AND either no
    ``nas.remote`` is configured or that remote is not present in the
    operator's ``rclone.conf`` (the same gate the §4.9 setup evaluator,
    ``INCOMPLETE_NO_NAS_REMOTE``, keys on).
    """
    from exlab_wizard.api._dependencies import nas_remote_available
    from exlab_wizard.constants import SyncMode

    has_nas_equipment = any(
        eq.sync_mode == SyncMode.NAS for eq in getattr(config, "equipment", ()) or ()
    )
    if not has_nas_equipment:
        return False
    nas = getattr(config, "nas", None)
    remote = getattr(nas, "remote", "") if nas is not None else ""
    return not remote or not nas_remote_available(deps, remote)


def _is_setup_ready(deps: Any) -> bool:
    """Return True when the §4.9 setup state is ``READY``.

    Delegates to :func:`api.setup.compute_setup_state` -- the single
    source of truth that ``GET /api/v1/setup/status``, the route gate,
    and the banner subline (:func:`_setup_next_action`) all consult --
    so the main-page setup-incomplete banner agrees with the API's
    verdict on every gate (paths, orchestrator, equipment, NAS
    credentials, and *both* LIMS branches).

    An earlier hand-rolled mirror checked only the LIMS *keyring*
    branch, so it kept the banner up for an otherwise-ready install
    whose LIMS slot is satisfied by ``offline_catalogue_path`` rather
    than a stored password -- a disconnected-workstation setup read as
    perpetually incomplete even though ``/setup/status`` reported
    ``ready`` (rclone-only migration follow-up, 2026-05-28).

    Best-effort: any evaluation failure degrades to "not ready" so a
    half-wired backend keeps the operator on the onboarding path rather
    than leaking a stack trace into the index route.
    """
    if deps is None or getattr(deps, "config", None) is None:
        return False
    try:
        from exlab_wizard.api.setup import compute_setup_state

        return compute_setup_state(deps) is SetupState.READY
    except Exception as exc:
        _log.warning("setup-readiness computation failed: %s", exc)
        return False


def _apply_autostart(deps: Any, enabled: bool) -> bool | None:
    """Register / unregister platform autostart; return the real post-op state.

    Returns ``deps.autostart_toggle``'s ``is_registered()`` result so callers
    (Settings -> Application) can reflect / revert the checkbox to reality;
    ``None`` when no toggle is wired or the op raised.
    """
    if deps is None:
        return None
    toggle: Callable[[bool], Any] | None = getattr(deps, "autostart_toggle", None)
    if toggle is None:
        return None
    try:
        return bool(toggle(enabled))
    except Exception as exc:
        _log.warning("autostart toggle failed: %s", exc)
        return None


def _build_main_state(
    deps: Any,
    *,
    selected_node: str | None = None,
    node_kind: str | None = None,
    is_received: bool = False,
    right_pane_collapsed: bool = False,
    selected_file_path: str | None = None,
    selected_file: dict[str, Any] | None = None,
    search_query: str = "",
    density: str = "",
) -> Any:
    from exlab_wizard.ui.components.status_bar_segment import derive_footer_segment_states
    from exlab_wizard.ui.pages import main as main_page

    # Redesign §3.1: orchestrator pipeline is always active; the staging
    # surface always renders, so MainPageState.orchestrator_enabled keeps
    # its True default. Folder-feed path mirrors the selected node so the
    # centre pane shows the right folder.
    ops_count, ops_input_required, ops_active = _operation_counts(deps)
    # Real Problems counts from the 30 s background audit (T6 / §B5).
    problems_hard = int(getattr(deps, "last_audit_hard", 0) or 0)
    # Footer status segments (Phase 5 / §3.5.5): Validator warns on a hard
    # finding; LIMS goes danger when the endpoint is unreachable. ``lims_reachable``
    # defaults True so a half-wired backend doesn't false-alarm; Staging has no
    # cheap cached count yet (a per-render list_staged_runs scan would be I/O on
    # the render path) so it stays NORMAL.
    lims_reachable = bool(getattr(deps, "lims_reachable", True)) if deps is not None else False
    footer_segments = derive_footer_segment_states(
        problems_count_hard=problems_hard,
        lims_reachable=lims_reachable,
    )
    return main_page.MainPageState(
        setup_incomplete=not _is_setup_ready(deps),
        setup_next_action=_setup_next_action(deps),
        selected_node=selected_node,
        selected_node_kind=node_kind,
        selected_node_is_received=is_received,
        right_pane_collapsed=right_pane_collapsed,
        folder_feed_path=selected_node,
        selected_file_path=selected_file_path,
        selected_file=selected_file,
        search_query=search_query,
        density=density,
        operations_count=ops_count,
        operations_input_required=ops_input_required,
        creation_in_flight=ops_active > 0,
        problems_count_hard=problems_hard,
        problems_count_soft=int(getattr(deps, "last_audit_soft", 0) or 0),
        validator_state=footer_segments.validator,
        lims_state=footer_segments.lims,
        staging_state=footer_segments.staging,
    )


def _panel_sessions(deps: Any) -> list[tuple[str, Any]]:
    """Return the (session_id, session) pairs the Operations panel shows.

    The §9.5 membership rule lives here only: everything except the
    terminal ``DONE`` / ``ABORTED`` (``FAILED`` stays so a recent failure
    is visible). Shared by :func:`_operation_counts` and
    :func:`_build_operation_rows` so the rule can't drift.
    """
    controller = getattr(deps, "controller", None) if deps is not None else None
    store = getattr(controller, "session_store", None) if controller is not None else None
    if store is None:
        return []
    from exlab_wizard.controller import on_operations_panel

    return [(sid, session) for sid, session in store.iter_sorted() if on_operations_panel(session)]


def _operation_counts(deps: Any) -> tuple[int, int, int]:
    """Return ``(panel_count, input_required, active)`` operation counts.

    ``panel_count`` is the §9.5 panel size (see :func:`_panel_sessions`).
    ``input_required`` counts suspended sessions awaiting a plugin answer
    (Frontend §9.5 / §3.5.5). ``active`` counts strictly non-terminal
    sessions and gates the §9.6 creation-button lock (``FAILED`` is
    terminal, so it sits in the panel but does not lock creation).
    """
    from exlab_wizard.controller import SessionState

    panel_rows = _panel_sessions(deps)
    input_required = sum(1 for _sid, s in panel_rows if s.state is SessionState.INPUT_REQUIRED)
    active = sum(1 for _sid, s in panel_rows if not s.is_terminal())
    return (len(panel_rows), input_required, active)


def _setup_next_action(deps: Any) -> str | None:
    """Return the §4.9.3 next-action string for the banner subline.

    Unlike :func:`_is_setup_ready` (a deliberately narrow LIMS-only
    readiness mirror), this consults the real evaluator via
    ``compute_setup_state`` so the banner names the actual first-failing
    gate -- notably the NAS-credentials gate added by the rclone-only
    migration (2026-05-26). Best-effort: any failure yields ``None`` so
    the banner falls back to its generic subline.
    """
    if deps is None:
        return None
    try:
        from exlab_wizard.api.setup import compute_setup_state
        from exlab_wizard.paths import setup_state_next_action

        action = setup_state_next_action(compute_setup_state(deps))
        return action.value if action is not None else None
    except Exception as exc:
        _log.warning("setup next-action computation failed: %s", exc)
        return None


def _build_main_query(
    selected: str,
    right_pane: str,
    *,
    file: str = "",
    q: str = "",
    density: str = "",
) -> str:
    """Compose the ``?selected=...&right_pane=...`` query string for /main.

    Omits each param when empty so the URL stays clean for default state.
    Used by every callback that re-navigates to /main with mutated state.
    Values are URL-encoded so node ids with spaces (project names like
    ``Cortex Q3 Pilot``) or other special characters survive the round
    trip back through the FastAPI query parser. The path separator
    ``/`` is intentionally preserved (``safe="/"``) so the encoded id
    stays human-readable in the address bar.

    Phase 4 (Option B / OQ-1/A) adds three optional params carried on the
    same URL/navigate model rather than a second (refreshable) paradigm:
    ``file`` (selected file/folder path in the centre list), ``q`` (search
    query), and ``density`` (file-list row density). Filesystem paths in
    ``file`` are URL-encoded (spaces / unicode) like ``selected``.
    """
    from urllib.parse import quote

    parts: list[str] = []
    if selected:
        parts.append(f"selected={quote(selected, safe='/')}")
    if right_pane:
        parts.append(f"right_pane={quote(right_pane, safe='/')}")
    if file:
        parts.append(f"file={quote(file, safe='/')}")
    if q:
        parts.append(f"q={quote(q, safe='')}")
    if density:
        parts.append(f"density={quote(density, safe='')}")
    return ("?" + "&".join(parts)) if parts else ""


def _classify_node(node_id: str | None, hierarchy: dict[Any, Any]) -> tuple[str | None, bool]:
    """Map a selected node id to ``(kind, is_received)``.

    Mirrors the shape classifier in ``tests/e2e/_test_app.py``: matches
    by node-id prefix against the hierarchy keys (equipment ids), then
    checks the path depth to discriminate equipment / project / run.

    Returns ``(None, False)`` for an unselected node or for a node id
    whose root equipment isn't in the hierarchy (defensive: the URL
    came from elsewhere, or the config changed since the link was
    captured).
    """
    if not node_id:
        return None, False
    from exlab_wizard.ui.components import tree as ui_tree

    owned_ids: set[str] = set()
    relay_ids: set[str] = set()
    for equipment_node in hierarchy:
        if not isinstance(equipment_node, ui_tree.EquipmentNode):
            continue
        if equipment_node.relay:
            relay_ids.add(equipment_node.equipment_id)
        else:
            owned_ids.add(equipment_node.equipment_id)
    root = node_id.split("/", 1)[0]
    if root not in owned_ids and root not in relay_ids:
        # The id's root equipment isn't in this device's tree -- treat
        # as unselected so the page doesn't render half-baked state.
        return None, False
    is_received = root in relay_ids
    if "/" not in node_id:
        kind = "received_equipment" if is_received else "equipment"
    elif "TestRun_" in node_id or "/Run_" in node_id:
        kind = "run"
    else:
        kind = "project"
    return kind, is_received


def _build_metadata_payload(
    node_id: str | None,
    node_kind: str | None,
    deps: Any,
) -> dict[str, Any]:
    """Build the metadata-pane payload for the selected node, by kind.

    Returns ``{}`` on any failure -- the metadata pane already tolerates
    a missing payload and renders the empty state. The shape per kind
    matches ``src/exlab_wizard/ui/components/metadata_pane.py``.
    """
    if not node_id or not node_kind:
        return {}
    config = getattr(deps, "config", None) if deps is not None else None
    if config is None:
        return {}
    try:
        if node_kind == "equipment":
            return _metadata_for_owned_equipment(node_id, config)
        if node_kind == "received_equipment":
            return _metadata_for_relay_equipment(node_id, config)
        if node_kind == "project":
            return _metadata_for_project(node_id, config)
        if node_kind == "run":
            return _metadata_for_run(node_id)
        return {}
    except Exception as exc:
        _log.warning("metadata payload build failed for %s (%s): %s", node_id, node_kind, exc)
        return {}


def _metadata_for_owned_equipment(node_id: str, config: Any) -> dict[str, Any]:
    """Project equipment-config fields into the owned-equipment payload."""
    for entry in getattr(config, "equipment", []):
        if entry.id != node_id:
            continue
        return {
            "id": entry.id,
            "label": entry.label or entry.id,
            "sync_mode": str(getattr(entry, "sync_mode", "")) or "nas",
            "local_root": entry.local_root or "",
            "nas_root": entry.nas_root or "",
        }
    return {}


def _metadata_for_relay_equipment(node_id: str, config: Any) -> dict[str, Any]:
    """Return the relay-equipment payload (label, source_host)."""
    from exlab_wizard.api.routers import browse as _browse

    for relay in _browse.build_received_equipment_nodes(config):
        if relay.id == node_id:
            return {
                "id": relay.id,
                "label": relay.label or relay.id,
                "source_host": "",  # populated by the relay producer's creation.json
            }
    return {"id": node_id, "label": node_id, "source_host": ""}


def _metadata_for_project(node_id: str, config: Any) -> dict[str, Any]:
    """Derive a project payload from the on-disk project directory.

    ``node_id`` is ``<equipment_id>/<project_name>``. Walks the project
    dir to count runs / test runs and reads the optional README.md for
    the objective summary; falls back to empty fields when the dir or
    README isn't present.
    """
    from exlab_wizard.constants import README_FILE_NAME, RUN_DIR_PREFIX, TEST_RUN_DIR_PREFIX

    parts = node_id.split("/", 1)
    if len(parts) != 2:
        return {}
    equipment_id, project_name = parts
    local_root = Path(getattr(config.paths, "local_root", "") or "")
    project_dir = local_root / equipment_id / project_name
    run_count = _count_dir_children(project_dir / "Runs", RUN_DIR_PREFIX)
    test_run_count = _count_dir_children(project_dir / "TestRuns", TEST_RUN_DIR_PREFIX)
    objective = ""
    try:
        objective = (
            (project_dir / README_FILE_NAME).read_text(encoding="utf-8").splitlines()[0][:120]
        )
    except (FileNotFoundError, OSError, IndexError):
        objective = ""
    return {
        "name": project_name,
        "short_id": project_name,
        "objective": objective,
        "run_count": run_count,
        "test_run_count": test_run_count,
    }


def _count_dir_children(parent: Path, prefix: str) -> int:
    """Count immediate child directories of ``parent`` whose names start
    with ``prefix``. Returns 0 when ``parent`` is missing / unreadable.
    """
    try:
        with os.scandir(parent) as iterator:
            return sum(
                1
                for entry in iterator
                if entry.name.startswith(prefix) and entry.is_dir(follow_symlinks=False)
            )
    except (FileNotFoundError, NotADirectoryError, PermissionError):
        return 0


def _metadata_for_run(node_id: str) -> dict[str, Any]:
    """Decode the run's creation.json into the metadata-pane payload.

    Returns ``{}`` on any parse error so the pane shows the empty state
    instead of crashing.
    """
    import msgspec

    from exlab_wizard.api.schemas import CreationJson
    from exlab_wizard.io import read_msgspec_json
    from exlab_wizard.paths import creation_json_path

    run_path = Path(node_id)
    cache_path = creation_json_path(run_path)
    if not cache_path.exists():
        return {"path": str(run_path), "name": run_path.name}
    try:
        payload = read_msgspec_json(cache_path, CreationJson)
    except (msgspec.DecodeError, msgspec.ValidationError):
        return {"path": str(run_path), "name": run_path.name}
    return {
        "label": payload.lims_project.name_at_creation,
        "name": run_path.name,
        "run_kind": str(payload.run_kind),
        "operator": payload.created_by or "",
        "objective": "",  # creation.json doesn't carry a separate objective today
        "template": payload.template.name if payload.template else "",
        "created_at": payload.created_at or "",
        "lims_project": payload.lims_project.short_id,
        "sync_status": payload.sync_status or "",
        "path": str(run_path),
    }


# ---------------------------------------------------------------------------
# Selection -> metadata payload (Phase 4, Option B / spec §4.3)
# ---------------------------------------------------------------------------


def _build_selected_file(
    file_path: str | None,
    feed_entries: list[Any],
    deps: Any,
) -> dict[str, Any] | None:
    """Resolve a centre-list selection (file or folder) to a render payload.

    Phase 4 / Option B (spec §4.3). ``file_path`` is the ``?file=`` query
    param -- the path the operator single-clicked in the Files pane. It is
    matched by path against the in-memory folder-feed entries (already
    fetched for the current node, so a file needs no extra I/O). A match
    that is a directory delegates to :func:`_build_selected_folder` for the
    one-level aggregate scan; a file match projects the feed entry's fields
    into a file-card payload.

    Returns ``None`` when ``file_path`` is empty or matches no current
    entry -- e.g. the file was removed since the click -- so the metadata
    pane shows no sub-card rather than an error (spec §7).

    File payload shape::

        {"kind": "file", "name", "path", "size", "modified",
         "sync_status", "tombstone"}

    ``size`` is pre-formatted via :func:`format_bytes`; a tombstone ("On
    NAS") row has no on-disk copy, so its ``size`` is ``None`` (spec §4.5).
    """
    if not file_path:
        return None
    match = next((e for e in feed_entries if getattr(e, "path", None) == file_path), None)
    if match is None:
        return None
    if getattr(match, "is_dir", False):
        return _build_selected_folder(match.name, match.path, deps)
    from exlab_wizard.ui.pages.staging import format_bytes

    size_bytes = getattr(match, "size_bytes", None)
    tombstone = bool(getattr(match, "tombstone", False))
    return {
        "kind": "file",
        "name": match.name,
        "path": match.path,
        "size": (None if tombstone or size_bytes is None else format_bytes(int(size_bytes))),
        "modified": getattr(match, "modified_iso", None),
        "sync_status": getattr(match, "sync_status", None),
        "tombstone": tombstone,
    }


def _build_selected_folder(
    name: str,
    path: str,
    deps: Any,
) -> dict[str, Any]:
    """Aggregate a one-level folder scan into a folder-card payload (OQ-4/B).

    Phase 4 / Option B (spec §4.3, §4.6). A folder selected in the centre
    list is summarised by a single-level :func:`scan_folder_sync`: its
    immediate ``item_count`` and a worst-of ``sync_rollup`` over the
    children's per-file sync states. No total size is computed -- the
    recursive walk is deferred (spec §11).

    A scan failure degrades to ``item_count=None`` + a neutral (``None``)
    rollup rather than raising, so a transient permission / vanished-folder
    error still renders a usable card (spec §7).

    Folder payload shape::

        {"kind": "folder", "name", "path", "item_count", "rollup"}
    """
    from exlab_wizard.api.routers import browse as _browse
    from exlab_wizard.ui.components.sync_rollup import sync_rollup

    config = getattr(deps, "config", None) if deps is not None else None
    item_count: int | None
    rollup: str | None
    try:
        response = _browse.scan_folder_sync(path, config)
        entries = list(getattr(response, "entries", []) or [])
        item_count = len(entries)
        rollup = sync_rollup(getattr(entry, "sync_status", None) for entry in entries)
    except Exception as exc:
        _log.warning("folder aggregate scan failed for %s: %s", path, exc)
        item_count = None
        rollup = None
    return {
        "kind": "folder",
        "name": name,
        "path": path,
        "item_count": item_count,
        "rollup": rollup,
    }


def _drive_folder_feed(app: Any, deps: Any, selected_path: str | None) -> list[Any]:
    """Mount / rebind the per-tab FolderFeed and return current entries.

    Uses ``app.storage.tab`` so one feed instance lives across navigations
    within a tab. Switching paths calls ``feed.start(new_path)`` which
    cancels the prior poll loop and starts a fresh one. Returns the most
    recent payload as a list of ``FileListEntry`` (empty when the feed
    hasn't ticked yet for this path).
    """
    from exlab_wizard.ui.client import folder_feed, refresh_coordinator
    from exlab_wizard.ui.components.file_list import FileListEntry

    if selected_path is None:
        return []
    try:
        tab_storage: Any = app.storage.tab
    except Exception:
        tab_storage = None
    coord_key = "folder_feed_coord"
    feed_key = "folder_feed"
    coord = None
    feed = None
    if tab_storage is not None:
        coord = tab_storage.get(coord_key)
        feed = tab_storage.get(feed_key)
    if coord is None:
        coord = refresh_coordinator.RefreshCoordinator()
        if tab_storage is not None:
            tab_storage[coord_key] = coord
    if feed is None:
        feed = folder_feed.FolderFeed(
            fetch=lambda p: _fetch_folder_async(deps, p, coord),
        )
        if tab_storage is not None:
            tab_storage[feed_key] = feed
    # Rebind to the current selection if it changed; FolderFeed.start
    # is idempotent for the same path. Reference is kept on tab storage
    # so it isn't garbage-collected mid-poll.
    if feed.state.path != selected_path:
        _spawn_background(feed.start(selected_path))
    payload = feed.state.last_payload
    if payload is None:
        return []
    # Payload is a FolderResponse (Pydantic) from scan_folder_sync.
    entries: list[Any] = []
    for entry in getattr(payload, "entries", []) or []:
        entries.append(
            FileListEntry(
                name=entry.name,
                path=entry.path,
                is_dir=entry.is_dir,
                size_bytes=entry.size_bytes,
                modified_iso=entry.modified_iso,
                sync_status=entry.sync_status,
                keep_local=getattr(entry, "keep_local", False),
                tombstone=getattr(entry, "tombstone", False),
            )
        )
    return entries


async def _fetch_folder_async(deps: Any, path: str, coord: Any) -> Any:
    """FolderFeed fetch hook. Skips when the tree just walked.

    Runs the synchronous ``scan_folder_sync`` helper in a thread to keep
    the asyncio loop responsive (matches the codebase's existing
    cache/equipment.py convention). Returns the raw FolderResponse;
    transient HTTPException / OSError are swallowed and surface as
    ``None`` so the feed keeps polling.
    """
    if coord is not None and coord.should_skip_folder():
        return None
    from exlab_wizard.api.routers import browse as _browse

    config = getattr(deps, "config", None) if deps is not None else None
    try:
        result = await asyncio.to_thread(_browse.scan_folder_sync, path, config)
    except Exception as exc:
        _log.debug("folder feed scan failed for %s: %s", path, exc)
        return None
    if coord is not None:
        coord.record_folder_refresh()
    return result


def _refresh_selected_folder(app: Any, deps: Any, selected_path: str | None) -> None:
    """Force a fresh single-folder scan and prime the feed payload (OQ-1/A).

    The Files-pane refresh button's mitigation for the navigate-per-click
    model (spec §4.6 / OQ-1/A): rather than wait for the folder feed's next
    poll tick, scan the current folder synchronously now and write the
    result onto the per-tab feed's ``last_payload`` so the immediate
    re-navigation renders fresh contents. Distinct from the toolbar's
    "Refresh everything" -- this re-scans only the open folder.

    A scan failure is swallowed to a WARN: the existing payload simply
    stays until the next poll (spec §7). A no-op when nothing is selected
    or the per-tab feed hasn't been mounted yet.
    """
    if selected_path is None:
        return
    from exlab_wizard.api.routers import browse as _browse

    config = getattr(deps, "config", None) if deps is not None else None
    try:
        payload = _browse.scan_folder_sync(selected_path, config)
    except Exception as exc:
        _log.warning("per-folder refresh scan failed for %s: %s", selected_path, exc)
        return
    try:
        tab_storage: Any = app.storage.tab
    except Exception:
        tab_storage = None
    feed = tab_storage.get("folder_feed") if tab_storage is not None else None
    if feed is not None and getattr(feed, "state", None) is not None:
        feed.state.last_payload = payload
        # A folder walk just happened, so record it on the coordinator -- the
        # same bookkeeping _fetch_folder_async does after its scan. This keeps
        # the manual refresh inside the coalescing window (should_skip_tree),
        # so an immediately-following tree poll won't redundantly re-walk.
        coord = tab_storage.get("folder_feed_coord") if tab_storage is not None else None
        if coord is not None:
            coord.record_folder_refresh()


def _run_staging_action(deps: Any, path: str, action: str, ui: Any) -> None:
    """Dispatch a per-run context action to its backend surface.

    Mirrors :func:`api.routers.staging.post_force_sync` /
    :func:`api.routers.staging.post_clear` but invokes the underlying
    primitives directly from the mount so the action stays in-process
    (no HTTP round trip from the same Python interpreter).
    """
    from exlab_wizard.ui.components.tree_context_menu import (
        RUN_CONTEXT_CLEAR_VERIFIED,
        RUN_CONTEXT_FORCE_SYNC,
        RUN_CONTEXT_VIEW_LOG,
    )

    config = getattr(deps, "config", None) if deps is not None else None
    if config is None:
        _show_toast(ui, "Run action unavailable: no config", positive=False)
        return
    run_path = Path(path)
    if action == RUN_CONTEXT_FORCE_SYNC:
        nas_sync = getattr(deps, "nas_sync", None) if deps is not None else None
        if nas_sync is None:
            _show_toast(ui, "Force-sync unavailable: NAS sync not wired", positive=False)
            return

        async def _do_enqueue() -> None:
            try:
                await nas_sync.enqueue(run_path)
            except Exception as exc:
                _log.exception("force-sync via mount failed")
                _show_toast(ui, f"Force-sync failed: {exc}", positive=False)
                return
            _show_toast(ui, f"Force-sync queued for {run_path.name}", positive=True)

        _spawn_background(_do_enqueue())
        return
    if action == RUN_CONTEXT_CLEAR_VERIFIED:

        async def _do_clear() -> None:
            try:
                files, _bytes = await asyncio.to_thread(clear_run_dir, run_path)
            except Exception as exc:
                _log.exception("per-run clear failed")
                _show_toast(ui, f"Clear failed: {exc}", positive=False)
                return
            if files == 0:
                _show_toast(ui, f"{run_path.name} already cleared", positive=True)
            else:
                _show_toast(ui, f"Cleared {files} file(s) from {run_path.name}", positive=True)

        _spawn_background(_do_clear())
        return
    if action == RUN_CONTEXT_VIEW_LOG:
        _open_log_dialog(deps, run_path, ui)
        return
    _show_toast(ui, f"Unknown run action: {action}", positive=False)


def _file_context_action(
    deps: Any,
    entry: Any,
    action: str,
    ui: Any,
    *,
    on_done: Callable[[], None] | None = None,
) -> None:
    """Handle ``Open in OS`` / ``Copy path`` / ``Keep local`` file actions."""
    from exlab_wizard.ui.components.file_list import (
        FILE_CONTEXT_COPY_PATH,
        FILE_CONTEXT_KEEP_LOCAL,
        FILE_CONTEXT_OPEN,
    )

    path = str(getattr(entry, "path", ""))
    if not path:
        _show_toast(ui, "No path on file entry", positive=False)
        return
    if action == FILE_CONTEXT_OPEN:
        if _open_in_os(path):
            _show_toast(ui, f"Opening {Path(path).name}", positive=True)
        else:
            _show_toast(ui, "Could not open file in OS", positive=False)
        return
    if action == FILE_CONTEXT_COPY_PATH:
        try:
            ui.clipboard.write(path)
        except Exception as exc:
            _log.warning("clipboard.write failed: %s", exc)
            _show_toast(ui, "Clipboard unavailable", positive=False)
            return
        _show_toast(ui, "Path copied to clipboard", positive=True)
        return
    if action == FILE_CONTEXT_KEEP_LOCAL:
        _toggle_keep_local(deps, entry, ui, on_done=on_done)
        return
    _show_toast(ui, f"Unknown file action: {action}", positive=False)


def _toggle_keep_local(
    deps: Any,
    entry: Any,
    ui: Any,
    *,
    on_done: Callable[[], None] | None = None,
) -> None:
    """Flip a file's ``keep_local`` flag via the orchestrator's writer.

    Operator-free per-file NAS sync design (2026-05-21): ``sync_state.json``
    has a single writer -- the orchestrator's :class:`SyncStateWriter` --
    so the GUI never writes the file directly. The mount calls
    ``set_keep_local`` in-process (matching the per-run staging actions),
    which is exactly what the ``POST /staging/{run}/keep-local`` endpoint
    does. The run root is resolved by walking up to the nearest
    ``creation.json`` cache.
    """
    from exlab_wizard.api.routers.browse import _find_run_root, _run_relative_posix

    writer = getattr(deps, "sync_state_writer", None) if deps is not None else None
    if writer is None:
        _show_toast(ui, "Keep-local unavailable: sync-state writer not wired", positive=False)
        return
    path = Path(str(getattr(entry, "path", "")))
    run_root = _find_run_root(path.parent)
    if run_root is None:
        _show_toast(ui, "Keep-local unavailable: file is not inside a run", positive=False)
        return
    rel = _run_relative_posix(run_root, path)
    if rel is None:
        _show_toast(ui, "Keep-local unavailable: could not resolve file path", positive=False)
        return
    new_value = not bool(getattr(entry, "keep_local", False))

    async def _do_toggle() -> None:
        try:
            await writer.set_keep_local(run_root, rel, new_value)
        except Exception as exc:
            _log.exception("keep-local toggle failed")
            _show_toast(ui, f"Keep-local failed: {exc}", positive=False)
            return
        verb = "kept local" if new_value else "no longer kept local"
        _show_toast(ui, f"{path.name} {verb}", positive=True)
        if on_done is not None:
            on_done()

    _spawn_background(_do_toggle())


def _open_in_os(path: str) -> bool:
    """Launch the host OS's default opener for ``path``.

    Returns False on platforms / failures we can't handle so the caller
    can surface a negative toast. The launch is fire-and-forget so the
    UI doesn't block waiting for the external app.
    """
    import subprocess
    import sys

    try:
        if sys.platform.startswith("linux"):
            subprocess.Popen(["xdg-open", path])
            return True
        if sys.platform == "darwin":
            subprocess.Popen(["open", path])
            return True
        if sys.platform == "win32":
            import os as _os

            _os.startfile(path)  # type: ignore[attr-defined]
            return True
    except Exception as exc:
        _log.warning("open_in_os failed for %s: %s", path, exc)
        return False
    return False


def _build_operation_rows(deps: Any) -> list[Any]:
    """Build the Operations-panel rows from the live session store (T3).

    Uses the shared §9.5 membership rule (:func:`_panel_sessions`): terminal
    ``DONE`` / ``ABORTED`` sessions fall off; ``FAILED`` stays so a recent
    failure is visible.
    """
    from exlab_wizard.ui.components.operations_modal import OperationRow

    return [OperationRow.from_session(sid, session) for sid, session in _panel_sessions(deps)]


def _open_operations_modal(deps: Any, ui: Any) -> None:
    """Open the in-flight Operations panel (Frontend §9.5).

    Builds a fresh snapshot on each open (true auto-refresh is the wizard's
    live stream, T2). Row actions dispatch to resume / cancel / details.
    """
    from exlab_wizard.ui.components.operations_modal import operations_modal

    controller = getattr(deps, "controller", None) if deps is not None else None
    if controller is None:
        _show_toast(ui, "Operations unavailable: controller not initialized", positive=False)
        return
    rows = _build_operation_rows(deps)
    dialog = operations_modal(
        rows,
        on_resume=lambda oid: _resume_operation(deps, oid, ui),
        on_cancel=lambda oid: _cancel_operation(deps, oid, ui),
        on_view_log=lambda oid: _open_operation_details(deps, oid, ui),
    )
    opener = getattr(dialog, "open", None)
    if callable(opener):
        opener()


def _open_operation_details(deps: Any, session_id: str, ui: Any) -> None:
    """Show a lightweight details/"log" dialog for one in-flight operation.

    The §9.5 "View log" action: surfaces the session's current state plus
    any suspend reason or error. (The NAS-sync run log is a separate,
    post-creation concern handled by :func:`_open_log_dialog`.)
    """
    controller = getattr(deps, "controller", None) if deps is not None else None
    store = getattr(controller, "session_store", None) if controller is not None else None
    session = store.get(session_id) if store is not None else None
    if session is None:
        _show_toast(ui, "Operation not found", positive=False)
        return
    state_val = getattr(session.state, "value", str(session.state))
    dialog = ui.dialog()
    with (
        dialog,
        ui.card().props('data-testid="operation-log-dialog"').style("min-width: 480px;"),
    ):
        ui.label(f"Operation {session_id}").style("font-weight: 600;")
        ui.label(f"State: {state_val}").style("color: var(--color-muted);")
        pending = getattr(session, "pending_input", None)
        if pending:
            ui.label(f"Awaiting input: {pending.get('reason', '')}").style(
                "font-family: var(--font-mono); font-size: 0.85em;"
            )
        error = getattr(session, "error", None)
        if error:
            ui.label(f"Error: {error.get('message', error.get('code', ''))}").style(
                "color: var(--color-danger); font-family: var(--font-mono); font-size: 0.85em;"
            )
    dialog.open()


def _resume_operation(deps: Any, session_id: str, ui: Any) -> None:
    """Resume a suspended session by re-opening its §9.1 input dialog (T5).

    Reads the parked ``pending_input`` (plugin / reason / fields) off the
    session and re-presents the escalation dialog; Submit resumes the
    pipeline with the answers.
    """
    controller = getattr(deps, "controller", None) if deps is not None else None
    store = getattr(controller, "session_store", None) if controller is not None else None
    session = store.get(session_id) if store is not None else None
    pending = getattr(session, "pending_input", None) if session is not None else None
    if controller is None or not pending:
        _show_toast(ui, "Nothing to resume: the operation is not awaiting input", positive=False)
        return
    _open_input_required_dialog(
        controller,
        session_id,
        ui,
        plugin=pending.get("plugin", ""),
        reason=pending.get("reason", ""),
        fields=pending.get("fields") or [],
    )


def _open_input_required_dialog(
    controller: Any,
    session_id: str,
    ui: Any,
    *,
    plugin: str,
    reason: str,
    fields: list[Any],
) -> Any:
    """Open the §9.1 escalation dialog; Submit resumes, Cancel confirms (T5).

    Submit calls ``controller.resume(session_id, values)`` -- the suspended
    pipeline wakes with the answers. ``resume`` raises on an unknown session
    or a stale (non-``INPUT_REQUIRED``) state; a plugin re-rejecting the
    values simply re-emits ``input_required`` (the consumer re-opens this
    dialog). Both are surfaced to the operator. Cancel routes through the
    §9.4 cancel dialog.
    Returns the dialog so the caller can force-close it on a terminal frame.
    """
    from exlab_wizard.ui.components.input_required_dialog import input_required_dialog

    def _on_submit(values: dict[str, Any]) -> None:
        async def _run() -> None:
            try:
                await controller.resume(session_id, values)
            except Exception as exc:
                _show_toast(ui, f"Could not submit input: {exc}", positive=False)

        _spawn_background(_run())

    def _on_cancel() -> None:
        _cancel_session(controller, session_id, ui)

    dialog = input_required_dialog(
        plugin=plugin,
        reason=reason,
        fields=fields,
        on_submit=_on_submit,
        on_cancel=_on_cancel,
    )
    opener = getattr(dialog, "open", None)
    if callable(opener):
        opener()
    return dialog


def _cancel_operation(deps: Any, session_id: str, ui: Any) -> None:
    """Resolve the controller off ``deps`` and open the §9.4 cancel dialog."""
    controller = getattr(deps, "controller", None) if deps is not None else None
    if controller is None:
        _show_toast(ui, "Cancel unavailable: controller not initialized", positive=False)
        return
    _cancel_session(controller, session_id, ui)


def _cancel_session(controller: Any, session_id: str, ui: Any) -> None:
    """Cancel an in-flight session via the §9.4 Discard / Keep dialog (T4).

    The operator chooses whether to discard the partially-created files
    (``discard_files=True`` -> ``shutil.rmtree`` of the partial dir) or
    keep them in place as an orphan. ``controller.cancel`` is a no-op on an
    already-terminal session; any error is surfaced as a toast.
    """
    dialog = ui.dialog()

    def _choose(discard_files: bool) -> None:
        dialog.close()

        async def _run() -> None:
            try:
                await controller.cancel(session_id, discard_files=discard_files)
                _show_toast(ui, "Operation cancelled", positive=True)
            except Exception as exc:
                _show_toast(ui, f"Cancel failed: {exc}", positive=False)

        _spawn_background(_run())

    with (
        dialog,
        ui.card().props('data-testid="cancel-confirm-dialog"').style("min-width: 420px;"),
    ):
        ui.label("Cancel this operation?").style("font-weight: 600;")
        ui.label("Discard the partially-created files, or keep them in place as an orphan?").style(
            "color: var(--color-muted);"
        )
        with ui.row().classes("justify-end w-full").style("gap: 0.5rem;"):
            ui.button("Back", on_click=lambda _e: dialog.close()).props("flat")
            ui.button("Keep files", on_click=lambda _e: _choose(False)).props(
                'flat data-testid="cancel-keep"'
            )
            ui.button("Discard files", on_click=lambda _e: _choose(True)).props(
                'flat color=negative data-testid="cancel-discard"'
            )
    dialog.open()


def _open_log_dialog(deps: Any, run_path: Path, ui: Any) -> None:
    """Open a NiceGUI dialog showing the run's sync-queue job state.

    The operator-free per-file NAS sync redesign (2026-05-21) removed
    ``ingest.json``; the per-run "log" is now the run's sync-queue job
    state. Phase 5/6 source this from the ``sync_state.json`` rollup.
    """

    async def _do_open() -> None:
        nas_sync = getattr(deps, "nas_sync", None) if deps is not None else None
        getter = getattr(nas_sync, "get_by_run_path", None) if nas_sync is not None else None
        row = None
        if getter is not None:
            try:
                row = await getter(run_path)
            except Exception as exc:  # pragma: no cover -- defensive
                _log.warning("sync-queue lookup failed for %s: %s", run_path, exc)
        state = getattr(getattr(row, "state", None), "value", None) or "none"
        try:
            dialog = ui.dialog()
            with (
                dialog,
                ui.card()
                .props('data-testid="run-log-dialog"')
                .style("min-width: 480px; max-width: 720px;"),
            ):
                ui.label(f"Log: {run_path.name}").style("font-weight: 600;")
                ui.label(f"Sync state: {state}").style("color: var(--color-muted);")
                if row is None:
                    ui.label("No sync job recorded for this run yet.").style(
                        "font-family: var(--font-mono); font-size: 0.85em;"
                    )
                else:
                    for field in ("enqueued_at", "verified_at", "attempts", "last_error"):
                        value = getattr(row, field, None)
                        if value:
                            ui.label(f"{field}: {value}").style(
                                "font-family: var(--font-mono); font-size: 0.85em;"
                            )
                ui.button("Close", on_click=dialog.close).props("flat")
            dialog.open()
        except Exception as exc:
            _log.warning("log dialog render failed: %s", exc)
            _show_toast(ui, "Log dialog unavailable", positive=False)

    _spawn_background(_do_open())


def _missing_setup_sections(deps: Any) -> tuple[str, ...]:
    """Return the settings sections the operator still needs to fill in.

    Mirrors a subset of the §4.9 setup-state evaluation: any setup-state
    other than READY surfaces at least one section. The Settings page
    uses this to auto-select the first incomplete section.
    """
    from exlab_wizard.api._dependencies import lims_password_present

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
    # rclone.conf NAS-sync migration: surface the NAS-remote section when
    # nas-mode equipment exist but the configured ``nas.remote`` is absent
    # from rclone.conf, so the setup-incomplete banner auto-selects it.
    # This mirrors the §4.9 setup gate (``INCOMPLETE_NO_NAS_REMOTE``).
    if _nas_remote_missing(deps, config):
        missing.append("nas_remote")
    if not config.lims.endpoint or not config.lims.email:
        missing.append("lims")
    if not lims_password_present(deps) and "lims" not in missing:
        missing.append("lims")
    return tuple(missing)


def _templates_dir(deps: Any) -> Path | None:
    """Return the configured templates directory, or ``None``."""
    config = getattr(deps, "config", None) if deps is not None else None
    if config is None or not config.paths.templates_dir:
        return None
    return Path(config.paths.templates_dir)


def _selected_template_path(deps: Any, state: Any) -> Path | None:
    """Return the absolute path of the template the wizard state selected.

    Prefers the resolved ``state.selected_template_path`` the template step
    stored -- this honours a per-instance (per-project / per-equipment)
    override the resolver picked, so the pipeline renders the exact file the
    operator saw (Backend Spec §5.0; design §4.3). Falls back to
    ``templates_dir / selected_template`` when the state carries no resolved
    path (e.g. the resolver was not wired), preserving the prior behaviour.
    Returns ``None`` when no template is selected and no fallback is
    derivable.
    """
    resolved = getattr(state, "selected_template_path", None)
    if resolved is not None:
        return Path(resolved)
    name = getattr(state, "selected_template", None)
    if not name:
        return None
    templates_dir = _templates_dir(deps)
    return templates_dir / name if templates_dir is not None else None


def _template_locations(deps: Any) -> list[tuple[str, str]]:
    """Return the manager's scope/location options as ``[(label, value)]``.

    Always offers ``("Global", "global")``; appends one
    ``("Equipment <id>", "equipment:<id>")`` per configured equipment so
    the operator can browse / edit per-equipment template stores. The
    global option is first so it stays the manager's default scope.
    """
    locations: list[tuple[str, str]] = [("Global", "global")]
    for equipment_id in _equipment_ids(deps):
        locations.append((f"Equipment {equipment_id}", f"equipment:{equipment_id}"))
    return locations


def _location_scan_dirs(deps: Any, loc: str) -> list[Path]:
    """Return the template directories the manager lists for ``loc``.

    ``"global"`` (or anything unrecognised) lists the flat
    ``paths.templates_dir``. ``"equipment:<id>"`` lists that equipment's
    type-segregated per-instance stores (``project/`` + ``run/`` under
    ``<local_root>/<id>/.exlab-wizard/templates/``) so editing reaches the
    per-equipment templates the resolver layers in. Missing directories are
    tolerated -- :func:`list_templates` returns nothing for them.
    """
    from exlab_wizard.constants import TemplateType
    from exlab_wizard.template.resolution import instance_template_dir

    if loc.startswith("equipment:"):
        equipment_id = loc.split(":", 1)[1]
        config = getattr(deps, "config", None) if deps is not None else None
        local_root = config.paths.local_root if config is not None else ""
        if equipment_id and local_root:
            equipment_dir = Path(local_root) / equipment_id
            return [
                instance_template_dir(equipment_dir, TemplateType.PROJECT.value),
                instance_template_dir(equipment_dir, TemplateType.RUN.value),
            ]
        return []

    templates_dir = _templates_dir(deps)
    return [templates_dir] if templates_dir is not None else []


def _template_names(
    deps: Any,
    template_type: str,
    *,
    equipment_id: str | None = None,
    project_path: Path | None = None,
) -> list[str]:
    """List the resolved template names a wizard should offer for ``template_type``.

    Resolves through :func:`exlab_wizard.template.resolution.resolve_template_chain`
    so per-instance (per-equipment / per-project) templates layer over the
    global ``templates_dir`` when an equipment / project context is known.
    The wizard page handlers call this at render time before the operator
    has picked equipment / project, so ``equipment_id`` / ``project_path``
    are typically ``None`` -- the resolver then returns the global templates
    (identical to the pre-resolver behaviour), degrading gracefully.
    """
    config = getattr(deps, "config", None) if deps is not None else None
    if config is None:
        return []
    try:
        from exlab_wizard.template.resolution import resolve_template_chain

        return [
            summary.name
            for summary in resolve_template_chain(
                config,
                template_type=template_type,
                equipment_id=equipment_id,
                project_path=project_path,
            )
        ]
    except Exception as exc:
        _log.warning("template scan failed: %s", exc)
        return []


def _template_questions_map(
    deps: Any,
    template_type: str,
    *,
    equipment_id: str | None = None,
    project_path: Path | None = None,
) -> dict[str, Any]:
    """Map each resolved template name to its parsed copier questions.

    Thin wrapper over :func:`_resolve_template_choices` that returns only the
    questions map (kept as a stable name for existing callers / tests).
    """
    return _resolve_template_choices(
        deps,
        template_type,
        equipment_id=equipment_id,
        project_path=project_path,
    ).questions


def _resolve_template_choices(
    deps: Any,
    template_type: str,
    *,
    equipment_id: str | None = None,
    project_path: Path | None = None,
    run_scope: str | None = None,
) -> TemplateChoices:
    """Resolve the templates a wizard should offer for one context.

    Resolves the chain through
    :func:`exlab_wizard.template.resolution.resolve_template_chain` (so
    per-instance templates layer over the global store when an equipment /
    project context is known), then resolves each template through the real
    ``TemplateEngine`` to extract its ``copier.yml`` questions. Returns a
    :class:`TemplateChoices` bundling the offered names (nearest scope
    first), the per-template questions (driving the dynamic Variables step),
    and the per-template **absolute resolved path** (so the wizard's submit
    renders the exact file listed, honouring a per-instance override rather
    than re-deriving ``templates_dir / name``). A template that fails to
    resolve is skipped with a WARN.
    """
    from exlab_wizard.template.resolution import TemplateChoices

    config = getattr(deps, "config", None) if deps is not None else None
    if config is None:
        return TemplateChoices()
    try:
        from exlab_wizard.constants import TemplateType
        from exlab_wizard.template.copier_driver import TemplateEngine
        from exlab_wizard.template.resolution import resolve_template_chain
        from exlab_wizard.ui.pages import templates as templates_page

        engine = TemplateEngine()
        scope = TemplateType(template_type)
        names: list[str] = []
        questions: dict[str, Any] = {}
        paths: dict[str, Path] = {}
        for summary in resolve_template_chain(
            config,
            template_type=template_type,
            equipment_id=equipment_id,
            project_path=project_path,
            run_scope=run_scope,
        ):
            try:
                resolved = engine.resolve(summary.path, scope)
            except Exception as exc:
                _log.warning("template %s failed to resolve: %s", summary.name, exc)
                continue
            names.append(summary.name)
            questions[summary.name] = templates_page.template_questions(resolved.raw_manifest)
            paths[summary.name] = summary.path
        return TemplateChoices(names=names, questions=questions, paths=paths)
    except Exception as exc:
        _log.warning("template question scan failed: %s", exc)
        return TemplateChoices()


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
        if catalogue is None:
            # schema_version mismatch -> treated as absent (§7.2.9.3).
            return []
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


async def _consume_session_progress(
    controller: Any, session_id: str, wizard_state: Any, ui: Any
) -> None:
    """Fold the controller's WS frames into the wizard's live phase bar (T2)
    and surface a plugin ``INPUT_REQUIRED`` escalation dialog (T5).

    Runs inside the wizard's submit coroutine (already bound to the page's
    client context), so re-rendering the ``@ui.refreshable`` progress view
    and opening dialogs are safe. Subscribing right after ``create_*``
    returns is race-free: ``_launch`` creates the session's event queue
    before the pipeline starts, so buffered early phases replay in order.
    On an ``input_required`` frame the §9.1 dialog opens; the loop keeps
    awaiting frames (the pipeline only resumes once the operator submits).
    A terminal ``done`` / ``failed`` frame force-closes any open dialog
    (e.g. the plugin timed out while suspended) and ends the loop.
    """
    from exlab_wizard.ui.components import session_progress

    progress = getattr(wizard_state, "progress", None)
    refresh = getattr(wizard_state, "progress_refresh", None)
    if progress is None:
        return
    open_dialog: Any = None
    try:
        async for frame in controller.subscribe(session_id):
            kind = frame.get("kind")
            if session_progress.apply_frame(progress, frame) and refresh is not None:
                with contextlib.suppress(Exception):
                    refresh()
            if kind == "input_required":
                open_dialog = _open_input_required_dialog(
                    controller,
                    session_id,
                    ui,
                    plugin=frame.get("plugin", ""),
                    reason=frame.get("reason", ""),
                    fields=frame.get("fields") or [],
                )
            if kind in ("done", "failed"):
                _close_dialog(open_dialog)
                break
    except Exception:
        _log.exception("progress consumer failed for session %s", session_id)


def _close_dialog(dialog: Any) -> None:
    """Best-effort close of a NiceGUI dialog (no-op when ``None`` / test mode)."""
    closer = getattr(dialog, "close", None)
    if callable(closer):
        with contextlib.suppress(Exception):
            closer()


async def _submit_project(deps: Any, state: Any, ui: Any) -> None:
    """Build a ProjectCreateRequest from the wizard state and run it."""
    controller = getattr(deps, "controller", None) if deps is not None else None
    if controller is None:
        _show_toast(ui, "Project creation unavailable: controller not initialized", positive=False)
        return
    if not state.selected_template:
        _show_toast(ui, "Pick a template before creating the project", positive=False)
        return
    template_path = _selected_template_path(deps, state)
    if template_path is None:
        _show_toast(ui, "Pick a template before creating the project", positive=False)
        return

    from exlab_wizard.controller.creation import ProjectCreateRequest

    readme = state.readme_fields
    request = ProjectCreateRequest(
        equipment_id=state.selected_equipment or "",
        template_path=template_path,
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
    await _run_creation(
        controller, controller.create_project, request, ui, label="Project", wizard_state=state
    )


async def _submit_run(deps: Any, state: Any, run_kind: RunKind, ui: Any) -> None:
    """Build a RunCreateRequest from the wizard state and run it."""
    controller = getattr(deps, "controller", None) if deps is not None else None
    if controller is None:
        _show_toast(ui, "Run creation unavailable: controller not initialized", positive=False)
        return
    if not state.selected_template:
        _show_toast(ui, "Pick a template before creating the run", positive=False)
        return
    template_path = _selected_template_path(deps, state)
    if template_path is None:
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
        template_path=template_path,
        run_kind=run_kind,
        variables=dict(state.template_variables),
        label=readme.get("label", ""),
        operator=readme.get("operator", ""),
        objective=readme.get("objective", ""),
    )
    kind_label = "Test run" if run_kind is RunKind.TEST else "Run"
    await _run_creation(
        controller, controller.create_run, request, ui, label=kind_label, wizard_state=state
    )


async def _run_creation(
    controller: Any,
    create_fn: Callable[[Any], Any],
    request: Any,
    ui: Any,
    *,
    label: str,
    wizard_state: Any = None,
) -> None:
    """Drive a create_* call to completion and toast the outcome.

    When ``wizard_state`` is supplied, the controller's phase stream is
    consumed live so the Confirm & Create step's progress bar advances as
    the pipeline runs (T2); otherwise the call just awaits the final state.
    """
    from exlab_wizard.controller import SessionState

    try:
        handle = await create_fn(request)
        if wizard_state is not None:
            await _consume_session_progress(controller, handle.session_id, wizard_state, ui)
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
    from exlab_wizard.constants import RunScope
    from exlab_wizard.template.resolution import project_dir
    from exlab_wizard.ui.pages import wizard_run as wizard_run_page

    state = wizard_run_page.RunWizardState(run_kind=run_kind)
    # Run templates are narrowed to the run kind's scope (a "test" run only
    # sees test/both templates; experimental sees experimental/both).
    scope = RunScope.TEST.value if run_kind is RunKind.TEST else RunScope.EXPERIMENTAL.value
    config = getattr(deps, "config", None) if deps is not None else None
    initial = _resolve_template_choices(deps, "run", run_scope=scope)

    def _resolve_run_templates(equipment_id: str | None, project_name: str | None) -> Any:
        # Per-project then per-equipment run templates layer over the global
        # store once the operator picks project + equipment (step 1, before
        # the template step). Backend Spec §5.0 / §3.2.
        proj_path = project_dir(config, equipment_id, project_name) if config is not None else None
        return _resolve_template_choices(
            deps, "run", equipment_id=equipment_id, project_path=proj_path, run_scope=scope
        )

    return wizard_run_page.render_run_wizard(
        state=state,
        templates=initial.names,
        equipment_ids=_equipment_ids(deps),
        template_questions=initial.questions,
        template_paths=initial.paths,
        on_resolve=_resolve_run_templates,
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
