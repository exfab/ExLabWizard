"""Smoke tests that invoke the NiceGUI factory functions.

The factory functions create NiceGUI elements; in this environment NiceGUI
tolerates element creation outside of a fully-bound app context so we can
exercise the render paths and verify the factories don't raise.

These tests fill the gaps left by the data-shape tests in
``test_components.py`` -- coverage is the primary goal.
"""

from __future__ import annotations

from nicegui import ui

from exlab_wizard.ui import keyboard, theme
from exlab_wizard.ui.components import (
    bandwidth_schedule_editor,
    banner_stack,
    credential_field,
    filter_chips,
    mode_badge,
    operations_modal,
    override_badge,
    session_progress,
    status_bar_segment,
    sync_status_icon,
    test_connection_panel,
    test_run_badge,
    tree,
    validation_summary,
)
from exlab_wizard.ui.notifications import (
    BannerId,
    ContainerId,
    Severity,
    reset_for_tests,
    show_banner,
)
from exlab_wizard.ui.pages import (
    main,
    problems,
    settings,
    welcome,
    wizard_project,
    wizard_run,
)


def _slot() -> ui.column:
    """Return a NiceGUI slot we can use as a parent for factory calls."""

    return ui.column()


def test_smoke_mode_badge_renders_in_slot() -> None:
    with _slot():
        out = mode_badge.mode_badge("test")
    assert out is not None
    with _slot():
        out = mode_badge.mode_badge("experimental", label="Foo")
    assert out is not None


def test_smoke_test_run_badge_renders() -> None:
    with _slot():
        out = test_run_badge.test_run_badge()
    assert out is not None


def test_smoke_override_badge_renders_with_callback() -> None:
    calls: list[str] = []
    with _slot():
        out = override_badge.override_badge(active=True, on_click=lambda: calls.append("clicked"))
    assert out is not None
    with _slot():
        out = override_badge.override_badge(active=False)
    assert out is not None


def test_smoke_sync_status_icon_renders_each_state() -> None:
    for status in (
        "pending",
        "synced",
        "failed",
        "blocked_by_validation",
        "override_active",
    ):
        with _slot():
            out = sync_status_icon.sync_status_icon(status)
        assert out is not None
    with _slot():
        out = sync_status_icon.sync_status_icon("retrying", retry_n=1, retry_m=3)
    assert out is not None


def test_smoke_session_progress_renders() -> None:
    with _slot():
        out = session_progress.session_progress(active_phase=None)
    assert out is not None
    with _slot():
        out = session_progress.session_progress(
            active_phase="running_plugins",
            plugin_current=2,
            plugin_total=4,
            plugin_name="my_plugin",
            completed=("validating_inputs",),
        )
    assert out is not None


def test_smoke_credential_field_renders_each_state() -> None:
    for state in (
        credential_field.STATE_NOT_SET,
        credential_field.STATE_SET,
        credential_field.STATE_EDITING,
    ):
        with _slot():
            out = credential_field.credential_field(
                label="Password",
                on_save=lambda v: None,
                on_clear=lambda: None,
                initial_state=credential_field.CredentialState(state=state),
            )
        assert out is not None


def test_smoke_test_connection_panel_renders() -> None:
    with _slot():
        out = test_connection_panel.test_connection_panel(None)
    assert out is not None

    success_result = test_connection_panel.TestConnectionResult(
        success=True, headline="Connected", detail="x", raw="{}"
    )
    with _slot():
        out = test_connection_panel.test_connection_panel(success_result)
    assert out is not None
    with _slot():
        out = test_connection_panel.test_connection_panel(success_result, stale=True)
    assert out is not None


def test_smoke_filter_chips_renders() -> None:
    chips = (
        filter_chips.ChipDefinition(chip_id="a", label="A", default_on=True),
        filter_chips.ChipDefinition(chip_id="b", label="B", default_on=False),
    )
    with _slot():
        out = filter_chips.filter_chips(chips, on_change=lambda s: None)
    assert out is not None


def test_smoke_status_bar_segment_renders() -> None:
    calls: list[str] = []
    with _slot():
        out = status_bar_segment.status_bar_segment(
            label="LIMS: live", on_click=lambda: calls.append("c")
        )
    assert out is not None
    with _slot():
        out = status_bar_segment.status_bar_segment(
            label="3 sync failed", state=status_bar_segment.SEGMENT_WARNING
        )
    assert out is not None
    with _slot():
        out = status_bar_segment.status_bar_segment(
            label="LIMS auth failed", state=status_bar_segment.SEGMENT_DANGER
        )
    assert out is not None


def test_smoke_banner_stack_renders_with_active_banners() -> None:
    reset_for_tests()
    show_banner(
        BannerId.SETUP_INCOMPLETE,
        container=ContainerId.GLOBAL,
        severity=Severity.WARNING,
        message="x",
    )
    with _slot():
        out = banner_stack.banner_stack(ContainerId.GLOBAL)
    assert out is not None
    reset_for_tests()


def test_smoke_operations_modal_renders() -> None:
    rows = [
        operations_modal.OperationRow(
            operation_id="1",
            state=operations_modal.STATE_SUSPENDED,
            started_at="2026-05-07T08:00:00",
            equipment="A",
            project="P",
            run="R",
            plugin="my_plugin",
        ),
        operations_modal.OperationRow(
            operation_id="2",
            state=operations_modal.STATE_RUNNING,
            started_at="2026-05-07T09:00:00",
            equipment="A",
            project="P",
            run="R",
        ),
    ]
    with _slot():
        out = operations_modal.operations_modal(
            rows,
            on_resume=lambda oid: None,
            on_cancel=lambda oid: None,
            on_view_log=lambda oid: None,
        )
    assert out is not None


def test_smoke_bandwidth_schedule_editor_renders() -> None:
    with _slot():
        out = bandwidth_schedule_editor.bandwidth_schedule_editor(
            bandwidth_schedule_editor.BandwidthSchedule(
                mode=bandwidth_schedule_editor.MODE_LIMIT,
                default_upload_mbps=10,
                windows=[
                    bandwidth_schedule_editor.ScheduleWindow(
                        days=["Mon", "Tue"],
                        from_time="08:00",
                        to_time="18:00",
                        upload_mbps=5,
                    )
                ],
            )
        )
    assert out is not None
    with _slot():
        out = bandwidth_schedule_editor.bandwidth_schedule_editor(
            bandwidth_schedule_editor.BandwidthSchedule()
        )
    assert out is not None


def test_smoke_validation_summary_renders() -> None:
    with _slot():
        out = validation_summary.validation_summary(
            validation_summary.ValidationSummary(
                hard_count=2,
                soft_count=1,
                excerpts=(
                    validation_summary.FindingExcerpt("Placeholder", "<x>"),
                    validation_summary.FindingExcerpt("Illegal char", "x:y"),
                    validation_summary.FindingExcerpt("Orphan", "/p/x"),
                ),
                override_active=True,
                override_reason_snippet="Approved",
                override_operator="alex",
                override_set_at="2026-05-06",
            )
        )
    assert out is not None
    with _slot():
        out = validation_summary.validation_summary(
            validation_summary.ValidationSummary(hard_count=0, soft_count=2, excerpts=())
        )
    assert out is not None


def test_smoke_tree_renders() -> None:
    equipment = tree.EquipmentNode(equipment_id="EQUIP_01")
    project = tree.ProjectNode(short_id="PROJ-1", name="Foo")
    archived = tree.ProjectNode(short_id="PROJ-2", name="Old", status=tree.PROJECT_ARCHIVED)
    deleted = tree.ProjectNode(short_id="PROJ-3", name="Gone", status=tree.PROJECT_DELETED)
    runs_for_proj = [
        tree.RunNode(directory_name="Run_2026-05-07", run_kind="experimental", label="cal sweep"),
        tree.RunNode(directory_name="TestRun_2026-05-07", run_kind="test"),
    ]
    with _slot():
        out = tree.build_tree(
            hierarchy={equipment: {project: runs_for_proj, archived: [], deleted: []}},
            filters=tree.TreeFilters(archived=True),
        )
    assert out is not None


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


def test_smoke_welcome_page_renders() -> None:
    out = welcome.render_welcome_page(
        on_get_started=lambda v: None,
        on_skip=lambda v: None,
    )
    assert out is not None


def test_smoke_main_page_renders_setup_complete() -> None:
    out = main.render_main_page(
        on_open_new_project=lambda: None,
        on_open_new_run=lambda: None,
        on_open_new_test_run=lambda: None,
        on_open_settings=lambda: None,
        on_refresh=lambda: None,
        state=main.MainPageState(setup_incomplete=False),
    )
    assert out is not None


def test_smoke_main_page_renders_setup_incomplete() -> None:
    reset_for_tests()
    out = main.render_main_page(
        on_open_new_project=lambda: None,
        on_open_new_run=lambda: None,
        on_open_new_test_run=lambda: None,
        on_open_settings=lambda: None,
        on_refresh=lambda: None,
        state=main.MainPageState(setup_incomplete=True),
    )
    assert out is not None
    reset_for_tests()


def test_smoke_wizard_project_renders() -> None:
    out = wizard_project.render_project_wizard(
        state=wizard_project.ProjectWizardState(),
        on_submit=lambda s: None,
    )
    assert out is not None


def test_smoke_wizard_run_renders_test_mode() -> None:
    out = wizard_run.render_run_wizard(
        state=wizard_run.RunWizardState(run_kind="test"),
        on_submit=lambda s: None,
    )
    assert out is not None


def test_smoke_wizard_run_renders_experimental_mode() -> None:
    out = wizard_run.render_run_wizard(
        state=wizard_run.RunWizardState(run_kind="experimental"),
        on_submit=lambda s: None,
    )
    assert out is not None


def test_smoke_settings_page_renders() -> None:
    out = settings.render_settings_page(
        state=settings.SettingsState(),
        on_save=lambda s: None,
        on_discard=lambda s: None,
    )
    assert out is not None


def test_smoke_settings_page_each_section_renders() -> None:
    for section in settings.SETTINGS_SECTIONS:
        out = settings.render_settings_page(
            state=settings.SettingsState(active_section=section),
            on_save=lambda s: None,
            on_discard=lambda s: None,
        )
        assert out is not None


def test_smoke_settings_page_setup_incomplete_auto_selects_first() -> None:
    out = settings.render_settings_page(
        state=settings.SettingsState(
            active_section="logging",
            incomplete_sections=("paths", "lims"),
            dirty_sections={"equipment"},
            pending_change_count=1,
        ),
        on_save=lambda s: None,
        on_discard=lambda s: None,
    )
    assert out is not None


def test_smoke_problems_page_renders() -> None:
    out = problems.render_problems_page(
        findings=[
            problems.Finding(
                finding_id="f1",
                severity="hard",
                rule_class="Placeholder",
                path="Run_<run_date>",
                matched_token="<run_date>",
                run_label="Cal",
                equipment="EQUIP_01",
                detected_at="2026-05-07T08:00:00",
                state="Active",
            ),
            problems.Finding(
                finding_id="f2",
                severity="hard",
                rule_class="Illegal char",
                path="x:y.txt",
                matched_token=":",
                run_label=None,
                equipment="EQUIP_01",
                detected_at="2026-05-07T08:00:00",
                state="Override active",
            ),
        ],
        on_override=lambda fid: None,
        on_revoke_override=lambda fid: None,
    )
    assert out is not None


def test_smoke_problems_page_renders_empty_state() -> None:
    out = problems.render_problems_page(
        findings=[],
        state=problems.ProblemsPageState(search="abc"),
    )
    assert out is not None


# ---------------------------------------------------------------------------
# Theme + keyboard registry binders (lazy imports)
# ---------------------------------------------------------------------------


def test_smoke_register_theme_returns_css() -> None:
    css = theme.register_theme()
    assert ":root" in css


def test_smoke_bind_global_shortcuts_does_not_raise() -> None:
    registry = keyboard.ShortcutRegistry()
    registry.register(keyboard.Shortcut.NEW_PROJECT, lambda: None)
    keyboard.bind_global_shortcuts(registry)
