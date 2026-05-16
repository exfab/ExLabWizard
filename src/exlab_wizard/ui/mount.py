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
    def _main(selected: str = "", right_pane: str = "") -> Any:
        deps = _deps()
        if _restart_gate(deps, ui):
            return None
        from exlab_wizard.api.routers import browse as _browse

        config = getattr(deps, "config", None)
        hierarchy = _browse.build_hierarchy_dict(config)
        selected_path = selected or None
        node_kind, is_received = _classify_node(selected_path, hierarchy)
        right_pane_collapsed = right_pane == "collapsed"
        state = _build_main_state(
            deps,
            selected_node=selected_path,
            node_kind=node_kind,
            is_received=is_received,
            right_pane_collapsed=right_pane_collapsed,
        )
        metadata_payload = _build_metadata_payload(selected_path, node_kind, deps)
        # Kick off / rebind the folder feed for the selected path and
        # gather the most recent payload (empty list on first render or
        # if the feed hasn't ticked yet).
        feed_entries = _drive_folder_feed(app, deps, selected_path)

        def _refresh() -> None:
            ui.navigate.to("/main" + _build_main_query(selected, right_pane))

        def _on_select_node(node_id: str) -> None:
            ui.navigate.to("/main" + _build_main_query(node_id, right_pane))

        def _on_toggle_right_pane() -> None:
            new_pane = "" if right_pane == "collapsed" else "collapsed"
            ui.navigate.to("/main" + _build_main_query(selected, new_pane))

        def _on_run_staging_action(path: str, action: str) -> None:
            _run_staging_action(deps, path, action, ui)

        def _on_clear_verified() -> None:
            _bulk_clear_verified(deps, ui)

        def _on_tree_context_action(node_id: str, action: str) -> None:
            # Either edit or remove deep-links into Settings with the
            # equipment pre-selected (Redesign §4.6 / decision 4A).
            del action  # both actions route to the same destination today
            ui.navigate.to(f"/settings?active=equipment&equipment_id={node_id}")

        def _on_file_context_action(entry: Any, action: str) -> None:
            _file_context_action(entry, action, ui)

        return main_page.render_file_explorer_page(
            on_open_new_project=lambda: ui.navigate.to("/wizard/project"),
            on_open_new_run=lambda: ui.navigate.to("/wizard/run"),
            on_open_new_test_run=lambda: ui.navigate.to("/wizard/test-run"),
            on_open_add_equipment=lambda: ui.navigate.to("/wizard/equipment"),
            on_open_settings=lambda: ui.navigate.to("/settings"),
            on_refresh=_refresh,
            on_select_node=_on_select_node,
            on_navigate_breadcrumb=_on_select_node,
            on_toggle_right_pane=_on_toggle_right_pane,
            on_run_staging_action=_on_run_staging_action,
            on_clear_verified=_on_clear_verified,
            on_tree_context_action=_on_tree_context_action,
            on_file_context_action=_on_file_context_action,
            state=state,
            hierarchy=hierarchy,
            file_list_entries=feed_entries,
            metadata_payload=metadata_payload,
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


def _build_main_state(
    deps: Any,
    *,
    selected_node: str | None = None,
    node_kind: str | None = None,
    is_received: bool = False,
    right_pane_collapsed: bool = False,
) -> Any:
    from exlab_wizard.ui.pages import main as main_page

    # Redesign §3.1: orchestrator pipeline is always active; the staging
    # surface always renders, so MainPageState.orchestrator_enabled keeps
    # its True default. Folder-feed path mirrors the selected node so the
    # centre pane shows the right folder.
    return main_page.MainPageState(
        setup_incomplete=not _is_setup_ready(deps),
        selected_node=selected_node,
        selected_node_kind=node_kind,
        selected_node_is_received=is_received,
        right_pane_collapsed=right_pane_collapsed,
        folder_feed_path=selected_node,
    )


def _build_main_query(selected: str, right_pane: str) -> str:
    """Compose the ``?selected=...&right_pane=...`` query string for /main.

    Omits each param when empty so the URL stays clean for default state.
    Used by every callback that re-navigates to /main with mutated state.
    """
    parts: list[str] = []
    if selected:
        parts.append(f"selected={selected}")
    if right_pane:
        parts.append(f"right_pane={right_pane}")
    return ("?" + "&".join(parts)) if parts else ""


def _classify_node(node_id: str | None, hierarchy: dict[Any, Any]) -> tuple[str | None, bool]:
    """Map a selected node id to ``(kind, is_received)``.

    Mirrors the shape classifier in ``tests/e2e/_test_app.py``: matches
    by node-id prefix against the hierarchy keys (equipment ids), then
    checks the path depth to discriminate equipment / project / run.

    Returns ``(None, False)`` for an unselected node.
    """
    if not node_id:
        return None, False
    from exlab_wizard.ui.components import tree as ui_tree

    relay_ids: set[str] = set()
    for equipment_node in hierarchy:
        if isinstance(equipment_node, ui_tree.EquipmentNode) and equipment_node.relay:
            relay_ids.add(equipment_node.equipment_id)
    root = node_id.split("/", 1)[0]
    is_received = root in relay_ids
    if "/" not in node_id:
        kind = "received_equipment" if is_received else "equipment"
    elif "TestRun_" in node_id or "/Run_" in node_id or node_id.startswith("Run_"):
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
    except Exception as exc:
        _log.warning("metadata payload build failed for %s (%s): %s", node_id, node_kind, exc)
        return {}
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
            "completeness_signal": getattr(entry, "completeness_signal", "") or "",
        }
    return {}


def _metadata_for_relay_equipment(node_id: str, config: Any) -> dict[str, Any]:
    """Return the relay-equipment payload (label, source_host)."""
    from exlab_wizard.api.routers import browse as _browse

    for relay in _browse._build_received_equipment_nodes(config):
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
    run_count = 0
    test_run_count = 0
    if project_dir.exists():
        for sub in project_dir.iterdir():
            if not sub.is_dir():
                continue
            if sub.name == "Runs":
                run_count = sum(
                    1
                    for child in sub.iterdir()
                    if child.is_dir() and child.name.startswith(RUN_DIR_PREFIX)
                )
            elif sub.name == "TestRuns":
                test_run_count = sum(
                    1
                    for child in sub.iterdir()
                    if child.is_dir() and child.name.startswith(TEST_RUN_DIR_PREFIX)
                )
    objective = ""
    readme_path = project_dir / README_FILE_NAME
    if readme_path.exists():
        try:
            objective = readme_path.read_text(encoding="utf-8").splitlines()[0][:120]
        except OSError:
            objective = ""
    return {
        "name": project_name,
        "short_id": project_name,
        "objective": objective,
        "run_count": run_count,
        "test_run_count": test_run_count,
    }


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


def _run_staging_action(deps: Any, path: str, action: str, ui: Any) -> None:
    """Dispatch a per-run context action to its backend surface.

    Mirrors :func:`api.routers.staging.post_force_sync` /
    :func:`api.routers.staging.post_clear` /
    :func:`api.routers.browse.get_run_log` but invokes the underlying
    primitives directly from the mount so the action stays in-process
    (no HTTP round trip from the same Python interpreter).
    """
    from exlab_wizard.cache.ingest_writer import IngestWriter

    config = getattr(deps, "config", None) if deps is not None else None
    if config is None:
        _show_toast(ui, "Staging action unavailable: no config", positive=False)
        return
    run_path = Path(path)
    if action == "force_sync":
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
    if action == "clear_verified":
        ingest_writer = getattr(deps, "ingest_writer", None) or IngestWriter()
        from exlab_wizard.orchestrator.cleanup import clear_run

        async def _do_clear() -> None:
            try:
                files, _bytes = await clear_run(
                    run_path, config=config, ingest_writer=ingest_writer
                )
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
    if action == "view_log":
        _open_log_dialog(run_path, ui)
        return
    _show_toast(ui, f"Unknown staging action: {action}", positive=False)


def _bulk_clear_verified(deps: Any, ui: Any) -> None:
    """Run the orchestrator's bulk ``clear_all_verified`` helper.

    Wired from the file-explorer footer's *Clear verified runs* button.
    Same in-process dispatch pattern as the per-run actions.
    """
    from exlab_wizard.cache.ingest_writer import IngestWriter
    from exlab_wizard.orchestrator.cleanup import clear_all_verified

    config = getattr(deps, "config", None) if deps is not None else None
    if config is None:
        _show_toast(ui, "Clear-verified unavailable: no config", positive=False)
        return
    ingest_writer = getattr(deps, "ingest_writer", None) or IngestWriter()

    async def _do_bulk() -> None:
        try:
            cleared = await clear_all_verified(config=config, ingest_writer=ingest_writer)
        except Exception as exc:
            _log.exception("bulk clear-verified failed")
            _show_toast(ui, f"Clear-verified failed: {exc}", positive=False)
            return
        if cleared:
            _show_toast(ui, f"Cleared {len(cleared)} verified run(s)", positive=True)
        else:
            _show_toast(ui, "No verified runs to clear", positive=True)

    _spawn_background(_do_bulk())


def _file_context_action(entry: Any, action: str, ui: Any) -> None:
    """Handle ``Open in OS`` / ``Copy path`` from the centre-pane file row."""
    from exlab_wizard.ui.components.file_list import FILE_CONTEXT_COPY_PATH, FILE_CONTEXT_OPEN

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
    _show_toast(ui, f"Unknown file action: {action}", positive=False)


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


def _open_log_dialog(run_path: Path, ui: Any) -> None:
    """Open a NiceGUI dialog showing the run's ingest.json history."""
    import msgspec

    from exlab_wizard.api.schemas import IngestJson
    from exlab_wizard.constants import CACHE_DIR_NAME, INGEST_JSON_NAME
    from exlab_wizard.io import read_msgspec_json

    ingest_path = run_path / CACHE_DIR_NAME / INGEST_JSON_NAME
    if not ingest_path.exists():
        _show_toast(ui, "No log: ingest.json not found", positive=False)
        return
    try:
        payload = read_msgspec_json(ingest_path, IngestJson)
    except (msgspec.DecodeError, msgspec.ValidationError) as exc:
        _show_toast(ui, f"Log unreadable: {exc}", positive=False)
        return
    try:
        dialog = ui.dialog()
        with dialog, ui.card().props('data-testid="run-log-dialog"').style(
            "min-width: 480px; max-width: 720px;"
        ):
            ui.label(f"Log: {run_path.name}").style("font-weight: 600;")
            ui.label(f"State: {payload.current_state}").style("color: var(--color-muted);")
            with ui.scroll_area().style("max-height: 360px;"):
                for entry in payload.history:
                    state_val = entry.get("state", "?") if isinstance(entry, dict) else "?"
                    at_val = entry.get("at", "") if isinstance(entry, dict) else ""
                    host_val = entry.get("host", "") if isinstance(entry, dict) else ""
                    ui.label(f"[{at_val}] {state_val} (host={host_val})").style(
                        "font-family: var(--font-mono); font-size: 0.85em;"
                    )
            ui.button("Close", on_click=dialog.close).props('flat')
        dialog.open()
    except Exception as exc:
        _log.warning("log dialog render failed: %s", exc)
        _show_toast(ui, "Log dialog unavailable", positive=False)


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
