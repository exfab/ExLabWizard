"""E2E test app wiring (Phase 16 follow-up).

Builds a FastAPI app via :func:`exlab_wizard.api.create_app` and mounts a
NiceGUI test surface at ``/`` with focused test pages keyed by route. The
e2e flows navigate to specific routes and assert against ``data-testid``
attributes on the rendered NiceGUI elements.

Routes:

* ``/`` -- welcome card (first-launch state).
* ``/main`` -- main window (toolbar + tree + tabs); ``?setup_incomplete=1``
  toggles the setup banner; ``?orchestrator=1`` shows the staging dock.
* ``/wizard/project`` -- new-project wizard (7 steps).
* ``/wizard/run`` -- new-run wizard (experimental, 6 steps).
* ``/wizard/test-run`` -- new-test-run wizard (test mode, 6 steps).
* ``/settings`` -- settings dialog (accepts ``?incomplete=paths,equipment``).
* ``/problems`` -- problems table (test fixtures injected via state).
* ``/staging`` -- orchestrator staging panel.

The app keeps a small piece of in-process state (under
``app.state.test_state``) so callbacks driven by the UI are observable
across navigations within a single test.

This module is **test-only**; no production code imports it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs

from fastapi import FastAPI

from exlab_wizard.api import create_app
from exlab_wizard.config.models import Config
from exlab_wizard.constants import Tier
from exlab_wizard.ui import notifications
from exlab_wizard.ui.notifications import BannerId, ContainerId, Severity
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
    welcome as welcome_page,
)
from exlab_wizard.ui.pages import (
    wizard_equipment as wizard_equipment_page,
)
from exlab_wizard.ui.pages import (
    wizard_project as wizard_project_page,
)
from exlab_wizard.ui.pages import (
    wizard_run as wizard_run_page,
)


@dataclass
class TestState:
    """Mutable cross-request state for the e2e harness."""

    setup_incomplete: bool = True
    welcomed: bool = False
    autostart_enabled: bool = True
    plugin_input_required: bool = False
    last_action: str = ""
    findings: list[problems_page.Finding] = field(default_factory=list)
    overrides: dict[str, str] = field(default_factory=dict)
    # Settings flow: ``config`` seeds the dialog (None == fresh install),
    # ``saved_config`` captures what the Save handler emitted.
    config: Config | None = None
    saved_config: Config | None = None
    # LIMS credential field: the keyring is mocked by this attribute --
    # ``None`` means "not set", a string is the captured password.
    lims_password: str | None = None
    # Redesign §6 — captured payload from the Add-Equipment wizard's
    # confirm step. ``None`` means the wizard hasn't confirmed yet.
    appended_equipment: Any = None
    # Redesign §4 — selected tree node id, used by the file-explorer
    # view to decide which folder-feed payload + metadata to render.
    selected_node: str | None = None
    selected_node_kind: str | None = None
    selected_node_is_received: bool = False
    # Seeded folder-feed payload keyed by tree-node id; the test app
    # serves these as the centre-pane file list.
    folder_feeds: dict[str, list[tuple[str, int, str | None]]] = field(default_factory=dict)
    # Seeded findings that the travelling-badge flow consumes (path → tier).
    seeded_findings: list[tuple[str, str]] = field(default_factory=list)


def _read_query(request_url: str) -> dict[str, list[str]]:
    """Parse a request URL's query string into a multi-value mapping."""
    if "?" not in request_url:
        return {}
    return parse_qs(request_url.split("?", 1)[1])


def _classify_test_node(node_id: str) -> tuple[str, bool]:
    """Same shape classifier the production mount uses, for seeded ids.

    Returns ``(kind, is_received)`` for a non-empty ``node_id``. Kept
    local to the test harness so seeded ids that aren't in any
    hierarchy (e.g. orphan run paths from staged-only fixtures) still
    classify cleanly.
    """
    if "/" not in node_id:
        if node_id.startswith("RELAY_"):
            return "received_equipment", True
        return "equipment", False
    if "Run_" in node_id or "TestRun_" in node_id:
        return "run", False
    return "project", False


def _seeded_metadata_payload(node_id: str | None, node_kind: str | None) -> dict[str, Any]:
    """Per-node-kind hardcoded payloads the test app threads into the
    production renderer's ``metadata_payload`` kwarg. Mirrors the
    seeded shapes the old inline ``_render_right_pane_seeded`` used.
    """
    if not node_id or not node_kind:
        return {}
    if node_kind == "equipment":
        return {
            "id": node_id,
            "label": "Confocal Microscope 1",
            "sync_mode": "stage" if "stage" in node_id else "nas",
            "local_root": "/data/lab",
            "nas_root": "//nas/lab",
            "completeness_signal": "sentinel_file",
        }
    if node_kind == "received_equipment":
        return {
            "id": node_id,
            "label": "Relayed Confocal",
            "source_host": "labpc-04",
        }
    if node_kind == "run":
        return {
            "label": "Demo run",
            "name": node_id.rsplit("/", 1)[-1],
            "run_kind": "experimental",
            "operator": "asmith",
            "objective": "demo",
            "template": "default",
            "created_at": "2026-05-14T09:22:00Z",
            "lims_project": "PROJ-0001",
            "sync_status": "synced",
            "path": node_id,
        }
    if node_kind == "project":
        parts = node_id.split("/", 1)
        name = parts[1] if len(parts) == 2 else node_id
        return {
            "name": name,
            "short_id": name,
            "objective": "Demo project objective",
            "run_count": 2,
            "test_run_count": 1,
        }
    return {}


def _seeded_file_entries(
    test_state: TestState, node_id: str | None
) -> list[Any]:
    """Build the centre-pane file rows the test flows assert on.

    Returns a default synthetic two-file feed unless the test seeded a
    specific path via ``test_state.folder_feeds``.
    """
    if node_id is None:
        return []
    from exlab_wizard.ui.components.file_list import FileListEntry

    rows = test_state.folder_feeds.get(
        node_id,
        [("scan.tif", 1024, "synced"), ("metadata.json", 256, "pending")],
    )
    return [
        FileListEntry(
            name=name,
            path=f"{node_id}/{name}",
            is_dir=False,
            size_bytes=size,
            modified_iso="2026-05-14T09:22:00Z",
            sync_status=sync,
        )
        for (name, size, sync) in rows
    ]


def build_test_app() -> FastAPI:
    """Construct the FastAPI app and mount the NiceGUI test surface."""
    from nicegui import ui

    from exlab_wizard.ui.theme import register_static_assets

    app = create_app()
    test_state = TestState()
    app.state.test_state = test_state

    # Mount the project's ``assets/`` directory at ``/assets`` so the
    # tree component's sync-icon SVGs (sync_local.svg / sync_cloud.svg)
    # resolve under e2e tests. Idempotent.
    register_static_assets()

    # ----------------------------------------------------------------------
    # Welcome (Flow 01)
    # ----------------------------------------------------------------------
    @ui.page("/")
    def welcome_index() -> None:
        def on_started(autostart: bool) -> None:
            test_state.autostart_enabled = autostart
            test_state.welcomed = True
            test_state.setup_incomplete = True
            test_state.last_action = "welcome.get_started"
            ui.navigate.to("/settings")

        def on_skip(autostart: bool) -> None:
            test_state.autostart_enabled = autostart
            test_state.welcomed = True
            test_state.last_action = "welcome.skip"
            ui.navigate.to("/main")

        ui.label(f"welcomed={test_state.welcomed}").props('data-testid="welcome-status"').style(
            "display:none;"
        )
        welcome_page.render_welcome_page(
            on_get_started=on_started,
            on_skip=on_skip,
        )

    # ----------------------------------------------------------------------
    # Main window — unified to the production redesign (Flow 05, 20, 24, ...)
    # ----------------------------------------------------------------------
    @ui.page("/main")
    def main_index(
        setup: int = 0,
        orchestrator: int = 0,
        seed_finding: str = "",
        selected: str = "",
        right_pane: str = "",
    ) -> None:
        from exlab_wizard.ui.components import tree as tree_component

        # Reset banner state per navigation so each test sees a fresh tree.
        notifications.reset_for_tests()

        # Comma-separated <path>:<tier> pairs the travelling-badge flow
        # consumes via the seeded tree below.
        if seed_finding:
            pairs: list[tuple[str, str]] = []
            for entry in seed_finding.split(","):
                if ":" in entry:
                    p, t = entry.rsplit(":", 1)
                    pairs.append((p, t))
            test_state.seeded_findings = pairs

        # The query-string-driven selection mirrors the production
        # mount's wiring so flows can land on a particular row without
        # a click-then-navigate race.
        selected_path = selected or None
        node_kind: str | None = None
        is_received = False
        if selected_path:
            node_kind, is_received = _classify_test_node(selected_path)
            test_state.selected_node = selected_path
            test_state.selected_node_kind = node_kind
            test_state.selected_node_is_received = is_received

        # Hierarchy used by every /main test. Owned EQ1 carries the
        # local + cleaned + test-run mix that flow 05b's sync-icon
        # assertions depend on; the relay-flagged RELAY_EQX root
        # surfaces the received-equipment row flow 18 / 24 target.
        hierarchy: dict[Any, Any] = {
            tree_component.EquipmentNode("EQ1", relay=False): {
                tree_component.ProjectNode("LIMS-001", "Demo Project"): [
                    tree_component.RunNode("Run_2026-05-07", "experimental", "Demo run"),
                    tree_component.RunNode(
                        directory_name="Run_2026-05-06",
                        run_kind="experimental",
                        label="Cleaned run",
                        sync_status="cleaned",
                    ),
                    tree_component.RunNode("TestRun_2026-05-07", "test", "Test run"),
                ],
            },
            tree_component.EquipmentNode("RELAY_EQX", relay=True): {
                tree_component.ProjectNode("PROJ-Relay", "Relayed Project"): [
                    tree_component.RunNode(
                        "Run_2026-05-14T09-22", "experimental", "Relayed run"
                    ),
                ],
            },
        }

        del orchestrator  # accepted for callers; the redesign always renders staging surfaces

        state = main_page.MainPageState(
            setup_incomplete=bool(setup),
            orchestrator_enabled=True,
            selected_node=selected_path,
            selected_node_kind=node_kind,
            selected_node_is_received=is_received,
            right_pane_collapsed=(right_pane == "collapsed"),
            folder_feed_path=selected_path,
        )

        def _on_open_new_project() -> None:
            test_state.last_action = "open.new_project"
            ui.navigate.to("/wizard/project")

        def _on_open_new_run() -> None:
            test_state.last_action = "open.new_run"
            ui.navigate.to("/wizard/run")

        def _on_open_new_test_run() -> None:
            test_state.last_action = "open.new_test_run"
            ui.navigate.to("/wizard/test-run")

        def _on_open_add_equipment() -> None:
            test_state.last_action = "open.add_equipment"
            ui.navigate.to("/wizard/equipment")

        def _on_open_settings() -> None:
            test_state.last_action = "open.settings"
            ui.navigate.to("/settings")

        def _on_refresh() -> None:
            test_state.last_action = "refresh"
            qs_parts: list[str] = []
            if selected:
                qs_parts.append(f"selected={selected}")
            if right_pane:
                qs_parts.append(f"right_pane={right_pane}")
            ui.navigate.to("/main" + ("?" + "&".join(qs_parts) if qs_parts else ""))

        def _on_select_node(node_id: str) -> None:
            test_state.last_action = f"select:{node_id}"
            qs = f"selected={node_id}"
            if right_pane:
                qs += f"&right_pane={right_pane}"
            ui.navigate.to(f"/main?{qs}")

        def _on_toggle_right_pane() -> None:
            new_pane = "" if right_pane == "collapsed" else "collapsed"
            test_state.last_action = f"toggle_right_pane:{new_pane or 'open'}"
            qs_parts: list[str] = []
            if selected:
                qs_parts.append(f"selected={selected}")
            if new_pane:
                qs_parts.append(f"right_pane={new_pane}")
            ui.navigate.to("/main" + ("?" + "&".join(qs_parts) if qs_parts else ""))

        def _on_run_staging_action(path: str, action: str) -> None:
            test_state.last_action = f"run.{action}:{path}"

        def _on_clear_verified() -> None:
            test_state.last_action = "clear_verified"

        def _on_tree_context_action(node_id: str, action: str) -> None:
            test_state.last_action = f"tree.{action}:{node_id}"
            ui.navigate.to(f"/settings?active=equipment&equipment_id={node_id}")

        def _on_file_context_action(entry: Any, action: str) -> None:
            test_state.last_action = f"file.{action}:{entry.path}"

        main_page.render_file_explorer_page(
            on_open_new_project=_on_open_new_project,
            on_open_new_run=_on_open_new_run,
            on_open_new_test_run=_on_open_new_test_run,
            on_open_add_equipment=_on_open_add_equipment,
            on_open_settings=_on_open_settings,
            on_refresh=_on_refresh,
            on_select_node=_on_select_node,
            on_navigate_breadcrumb=_on_select_node,
            on_toggle_right_pane=_on_toggle_right_pane,
            on_run_staging_action=_on_run_staging_action,
            on_clear_verified=_on_clear_verified,
            on_tree_context_action=_on_tree_context_action,
            on_file_context_action=_on_file_context_action,
            state=state,
            hierarchy=hierarchy,
            file_list_entries=_seeded_file_entries(test_state, selected_path),
            metadata_payload=_seeded_metadata_payload(selected_path, node_kind),
            # Expand the whole tree up front so e2e tests see every run
            # row (and its sync icon) in the DOM without clicking carets.
            tree_expand_all=True,
        )

    # ----------------------------------------------------------------------
    # Project wizard (Flow 02)
    # ----------------------------------------------------------------------
    @ui.page("/wizard/project")
    def project_wizard_index() -> None:
        s = wizard_project_page.ProjectWizardState(
            selected_lims_short_id="LIMS-001",
            selected_template="default",
            selected_equipment="EQ1",
            template_variables={},
            readme_fields={"label": "demo", "operator": "asmith", "objective": "demo run"},
        )

        def _submit(state: wizard_project_page.ProjectWizardState) -> None:
            test_state.last_action = "wizard.project.submit"
            # Render a confirm-card stand-in so tests see the success path
            ui.label("Project created at /tmp/data/EQ1/LIMS-001").props(
                'data-testid="wizard-project-success"'
            )

        wizard_project_page.render_project_wizard(
            state=s, on_submit=_submit, on_cancel=lambda: ui.navigate.to("/main")
        )

    # ----------------------------------------------------------------------
    # Run wizard, experimental (Flow 03)
    # ----------------------------------------------------------------------
    @ui.page("/wizard/run")
    def run_wizard_index() -> None:
        s = wizard_run_page.RunWizardState(
            run_kind="experimental",
            selected_project_name="Demo Project",
            selected_equipment="EQ1",
            selected_template="default",
            template_variables={},
            readme_fields={"label": "demo", "operator": "asmith", "objective": "demo run"},
        )

        def _submit(state: wizard_run_page.RunWizardState) -> None:
            test_state.last_action = f"wizard.run.{state.run_kind}.submit"
            ui.label("Run created").props('data-testid="wizard-run-success"')

        wizard_run_page.render_run_wizard(
            state=s, on_submit=_submit, on_cancel=lambda: ui.navigate.to("/main")
        )

    # ----------------------------------------------------------------------
    # Run wizard, test mode (Flow 04)
    # ----------------------------------------------------------------------
    @ui.page("/wizard/test-run")
    def test_run_wizard_index() -> None:
        s = wizard_run_page.RunWizardState(
            run_kind="test",
            selected_project_name="Demo Project",
            selected_equipment="EQ1",
            selected_template="default",
            template_variables={},
            readme_fields={"label": "demo", "operator": "asmith", "objective": "demo run"},
        )

        def _submit(state: wizard_run_page.RunWizardState) -> None:
            test_state.last_action = f"wizard.run.{state.run_kind}.submit"
            ui.label("Test run created").props('data-testid="wizard-run-success"')

        wizard_run_page.render_run_wizard(
            state=s, on_submit=_submit, on_cancel=lambda: ui.navigate.to("/main")
        )

    # ----------------------------------------------------------------------
    # Add-Equipment wizard (Flow 16 -- Redesign §6)
    # ----------------------------------------------------------------------
    @ui.page("/wizard/equipment")
    def wizard_equipment_index(step: str = "identity") -> None:
        state = wizard_equipment_page.EquipmentWizardState(
            active_step=step or "identity",
            equipment_id="FLOW_99",
            label="Flow Cytometer 99",
            local_root="/data",
            nas_root="/srv/nas",
            rclone_remote="lab-nas",
            rclone_remote_path="lab/FLOW_99",
            sentinel_filename="done.flag",
        )

        def _advance(current: str) -> None:
            idx = wizard_equipment_page.EQUIPMENT_WIZARD_STEPS.index(current)
            if idx + 1 < len(wizard_equipment_page.EQUIPMENT_WIZARD_STEPS):
                ui.navigate.to(
                    f"/wizard/equipment?step={wizard_equipment_page.EQUIPMENT_WIZARD_STEPS[idx + 1]}"
                )

        def _back(current: str) -> None:
            idx = wizard_equipment_page.EQUIPMENT_WIZARD_STEPS.index(current)
            if idx > 0:
                ui.navigate.to(
                    f"/wizard/equipment?step={wizard_equipment_page.EQUIPMENT_WIZARD_STEPS[idx - 1]}"
                )

        def _confirm(eq: Any) -> None:
            test_state.appended_equipment = eq
            test_state.last_action = "wizard.equipment.confirm"
            ui.label("Equipment added").props('data-testid="wizard-equipment-success"')

        wizard_equipment_page.render_wizard_equipment(
            state=state,
            on_advance=_advance,
            on_back=_back,
            on_confirm=_confirm,
            on_cancel=lambda: ui.navigate.to("/main"),
        )

    # ----------------------------------------------------------------------
    # Settings (Flow 08)
    # ----------------------------------------------------------------------
    @ui.page("/settings")
    def settings_index(
        incomplete: str = "",
        active: str = "",
    ) -> None:
        incomplete_sections = (
            tuple(sec for sec in incomplete.split(",") if sec) if incomplete else ()
        )
        active_section = active or "paths"
        s = settings_page.SettingsState(
            incomplete_sections=incomplete_sections,
            active_section=active_section,
        )

        def _save(updated: Config) -> None:
            test_state.last_action = "settings.save"
            test_state.setup_incomplete = False
            test_state.saved_config = updated
            ui.label("Settings saved").props('data-testid="settings-saved"')

        def _discard(state: settings_page.SettingsState) -> None:
            test_state.last_action = "settings.discard"

        def _select_section(section: str) -> None:
            test_state.last_action = f"settings.select:{section}"
            ui.navigate.to(f"/settings?incomplete={incomplete}&active={section}")

        def _save_lims_password(value: str) -> None:
            # Stands in for the OS keyring write (Frontend Spec §7.4.1).
            test_state.last_action = "settings.lims_password.save"
            test_state.lims_password = value

        def _clear_lims_password() -> None:
            test_state.last_action = "settings.lims_password.clear"
            test_state.lims_password = None

        settings_page.render_settings_page(
            config=test_state.config,
            state=s,
            on_save=_save,
            on_discard=_discard,
            on_select_section=_select_section,
            on_save_lims_password=_save_lims_password,
            on_clear_lims_password=_clear_lims_password,
            lims_password_present=test_state.lims_password is not None,
        )

    # ----------------------------------------------------------------------
    # Problems (Flow 06, 10, 11)
    # ----------------------------------------------------------------------
    @ui.page("/problems")
    def problems_index(seed: str = "", reset: int = 0) -> None:
        # Seed only when the operator (test) explicitly asks for a seed
        # AND the findings list is empty / a reset is requested. Re-seeding
        # on every nav would clobber the override / revoke round-trip.
        if reset:
            test_state.findings = []
            test_state.overrides.clear()
        if seed and not test_state.findings:
            if seed == "hard":
                test_state.findings = [
                    problems_page.Finding(
                        finding_id="F-1",
                        severity=Tier.HARD,
                        rule_class="Placeholder",
                        path="/data/EQ1/LIMS-001/Run_2026-05-07",
                        matched_token="<placeholder>",
                        run_label="Run_2026-05-07",
                        equipment="EQ1",
                        detected_at="2026-05-07T10:00:00Z",
                        state="Active",
                    ),
                ]
            elif seed == "schema_mismatch":
                test_state.findings = [
                    problems_page.Finding(
                        finding_id="F-2",
                        severity=Tier.HARD,
                        rule_class="Missing field",
                        path="/data/EQ1/LIMS-001/Run_2026-05-07/.exlab-wizard/creation.json",
                        matched_token="schema_version=2.0",
                        run_label="Run_2026-05-07",
                        equipment="EQ1",
                        detected_at="2026-05-07T10:00:00Z",
                        state="Active",
                    ),
                ]
            elif seed == "orphan":
                test_state.findings = [
                    problems_page.Finding(
                        finding_id="F-3",
                        severity=Tier.HARD,
                        rule_class="Orphan",
                        path="/data/EQ1/LIMS-001/Run_2026-05-07-orphan",
                        matched_token="missing creation.json",
                        run_label="Run_2026-05-07-orphan",
                        equipment="EQ1",
                        detected_at="2026-05-07T10:00:00Z",
                        state="Active",
                    ),
                ]

        def _override(finding_id: str) -> None:
            test_state.last_action = f"problems.override:{finding_id}"
            test_state.overrides[finding_id] = "operator-supplied reason"
            test_state.findings = [
                problems_page.Finding(
                    finding_id=f.finding_id,
                    severity=f.severity,
                    rule_class=f.rule_class,
                    path=f.path,
                    matched_token=f.matched_token,
                    run_label=f.run_label,
                    equipment=f.equipment,
                    detected_at=f.detected_at,
                    state="Override active" if f.finding_id == finding_id else f.state,
                )
                for f in test_state.findings
            ]
            ui.navigate.to(f"/problems?seed={seed}&_t={len(test_state.overrides)}")

        def _revoke(finding_id: str) -> None:
            test_state.last_action = f"problems.revoke:{finding_id}"
            test_state.overrides.pop(finding_id, None)
            test_state.findings = [
                problems_page.Finding(
                    finding_id=f.finding_id,
                    severity=f.severity,
                    rule_class=f.rule_class,
                    path=f.path,
                    matched_token=f.matched_token,
                    run_label=f.run_label,
                    equipment=f.equipment,
                    detected_at=f.detected_at,
                    state="Active" if f.finding_id == finding_id else f.state,
                )
                for f in test_state.findings
            ]
            ui.navigate.to(f"/problems?seed={seed}")

        # Show both Active and Override active by default so the
        # override / revoke round-trip is visible in the table without
        # an extra filter-chip click.
        from exlab_wizard.ui.components import filter_chips

        problems_state = problems_page.ProblemsPageState(
            state_chips=filter_chips.ChipState(
                active={"Active", "Override active"},
            ),
        )
        problems_page.render_problems_page(
            findings=list(test_state.findings),
            state=problems_state,
            on_override=_override,
            on_revoke_override=_revoke,
        )

    # ----------------------------------------------------------------------
    # Staging dock (Flow 09)
    # ----------------------------------------------------------------------
    @ui.page("/staging")
    def staging_index(state: str = "staging") -> None:
        from exlab_wizard.constants import IngestState
        from exlab_wizard.orchestrator.staging_query import StagedRunSummary

        rows = [
            StagedRunSummary(
                path="/staging/EQ1/LIMS-001/Run_2026-05-07",
                equipment_id="EQ1",
                project_name="LIMS-001",
                run_kind="experimental",
                current_state=state,
                file_count=12,
                byte_total=1048576,
                elapsed_seconds_since_last_activity=42,
                last_activity_at="2026-05-07T10:00:00Z",
            )
        ]

        def _force_sync(path: str) -> None:
            test_state.last_action = f"staging.force_sync:{path}"
            ui.navigate.to(f"/staging?state={IngestState.SYNC_QUEUED.value}")

        def _clear(path: str) -> None:
            test_state.last_action = f"staging.clear:{path}"
            ui.navigate.to(f"/staging?state={IngestState.CLEARED.value}")

        def _view_log(path: str) -> None:
            test_state.last_action = f"staging.view_log:{path}"

        def _clear_verified() -> None:
            test_state.last_action = "staging.clear_verified"

        dock_state = staging_page.StagingDockState(
            rows=rows,
            on_force_sync=_force_sync,
            on_clear=_clear,
            on_view_log=_view_log,
            on_clear_verified=_clear_verified,
        )
        staging_page.render_staging_dock(dock_state)

    # ----------------------------------------------------------------------
    # Plugin input dialog (Flow 07)
    # ----------------------------------------------------------------------
    @ui.page("/plugin-input")
    def plugin_input_index() -> None:
        with (
            ui.card()
            .props('data-testid="plugin-input-dialog"')
            .style("padding: var(--sp-6); max-width: 480px;")
        ):
            ui.label("Plugin input required").props('data-testid="plugin-input-headline"')
            field = ui.input(label="Operator initials").props(
                'data-testid="plugin-input-field-operator_initials"'
            )

            def _submit() -> None:
                test_state.last_action = f"plugin_input.submit:{field.value}"
                ui.navigate.to(
                    f"/wizard/project?resumed=1&v={field.value}",
                )

            def _cancel() -> None:
                test_state.last_action = "plugin_input.cancel"
                ui.navigate.to("/main")

            with ui.row().style("gap: var(--sp-3);"):
                ui.button("Submit", on_click=lambda _evt: _submit()).props(
                    'color=primary data-testid="plugin-input-submit"'
                )
                ui.button("Cancel", on_click=lambda _evt: _cancel()).props(
                    'flat data-testid="plugin-input-cancel"'
                )

    # ----------------------------------------------------------------------
    # Notifications playground (Flow 14)
    # ----------------------------------------------------------------------
    @ui.page("/notifications")
    def notifications_index(banner: str = "") -> None:
        notifications.reset_for_tests()
        message_map = {
            BannerId.SETUP_INCOMPLETE: "Setup is incomplete; configure the highlighted sections.",
            BannerId.SYNC_BLOCKED_ON_SUCCESS_CARD: (
                "Sync is blocked: a hard finding requires action."
            ),
            BannerId.LIMS_UNREACHABLE: "LIMS unreachable; using cached project list.",
            BannerId.NAS_UNREACHABLE: "NAS unreachable; runs will queue locally.",
            BannerId.RECONNECTING: "Reconnecting...",
        }
        if banner:
            try:
                bid = BannerId(banner)
                notifications.show_banner(
                    bid,
                    container=ContainerId.GLOBAL,
                    severity=Severity.WARNING,
                    message=message_map[bid],
                )
            except ValueError:
                pass

        from exlab_wizard.ui.components import banner_stack

        banner_stack.banner_stack(ContainerId.GLOBAL)

    # ----------------------------------------------------------------------
    # Keyboard shortcuts target (Flow 13)
    # ----------------------------------------------------------------------
    @ui.page("/keyboard")
    def keyboard_index() -> None:
        # Render the main page with no state changes; use JS to listen for
        # specific shortcuts and surface them via a hidden marker element.
        ui.add_head_html("""
        <script>
            window.addEventListener('keydown', function(e) {
                let marker = document.querySelector('[data-testid="keyboard-marker"]');
                if (!marker) return;
                if ((e.ctrlKey || e.metaKey) && e.key === 'n') {
                    marker.setAttribute('data-action', 'new-project');
                    e.preventDefault();
                } else if (e.key === 'Escape') {
                    marker.setAttribute('data-action', 'escape');
                }
            });
        </script>
        """)
        ui.element("div").props('data-testid="keyboard-marker" data-action="none"').style(
            "display:none;"
        )
        ui.label("Keyboard test page").props('data-testid="keyboard-page-loaded"')

    # ----------------------------------------------------------------------
    # WebSocket reconnect target (Flow 15)
    # ----------------------------------------------------------------------
    @ui.page("/reconnect")
    def reconnect_index() -> None:
        from exlab_wizard.ui.components import banner_stack

        notifications.reset_for_tests()
        notifications.show_banner(
            BannerId.RECONNECTING,
            container=ContainerId.GLOBAL,
            severity=Severity.INFO,
            message="Reconnecting...",
            dismissible=False,
        )
        banner_stack.banner_stack(ContainerId.GLOBAL)

    ui.run_with(
        app,
        mount_path="/",
        show_welcome_message=False,
        storage_secret="e2e-test-secret",
    )
    return app


def create_app_factory() -> FastAPI:
    """uvicorn ``--factory`` entrypoint."""
    return build_test_app()
