"""Unit tests for UI pages.

Pages render NiceGUI elements; these tests exercise the data-shape /
state-machine surfaces (steppers, banners, filter routing) rather than
the visual output. Visual concerns are covered by Phase-16 Playwright
tests.
"""

from __future__ import annotations

from exlab_wizard.ui.pages import (
    main,
    problems,
    settings,
    welcome,
    wizard_project,
    wizard_run,
)

# ---------------------------------------------------------------------------
# welcome
# ---------------------------------------------------------------------------


def test_welcome_card_spec_default_autostart_on() -> None:
    """Welcome card defaults autostart to on (Frontend §3.1.3)."""

    spec = welcome.welcome_card_spec()
    assert spec.autostart_default_on is True
    assert spec.headline == "Welcome to ExLab-Wizard"
    assert "5 minutes" in spec.time_estimate
    assert spec.primary_label == "Get started"
    assert spec.secondary_label == "Skip for now"


def test_welcome_card_three_bullets() -> None:
    """Three bullets describing what the app does.

    Redesign decision 2: bullets reworded for the multi-equipment /
    file-explorer framing. The earlier LIMS-centric bullet collapsed
    into the second (live-folder-view) framing; NAS sync still
    references hard-tier findings.
    """

    spec = welcome.welcome_card_spec()
    assert len(spec.bullets) == 3
    assert any("equipment" in b.lower() for b in spec.bullets)
    assert any("NAS sync" in b for b in spec.bullets)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def test_main_default_chips_match_spec() -> None:
    """Active default-on, Archived default-off, Test runs default-on."""

    chips = main._default_chips()
    by_id = {c.chip_id: c for c in chips}
    assert by_id["active"].default_on is True
    assert by_id["archived"].default_on is False
    assert by_id["test_runs"].default_on is True


def test_main_chip_state_to_tree_filters_round_trip() -> None:
    """Translation preserves the chip state."""

    state = main.MainPageState()
    filters = main.chip_state_to_tree_filters(state.chip_state)
    assert filters.active is True
    assert filters.archived is False
    assert filters.test_runs is True


def test_main_problems_badge_simple_count_when_no_soft() -> None:
    state = main.MainPageState(problems_count_hard=3, problems_count_soft=0)
    assert main.problems_badge_text(state) == "3"


def test_main_problems_badge_compound_when_soft_present() -> None:
    state = main.MainPageState(problems_count_hard=3, problems_count_soft=12)
    assert main.problems_badge_text(state) == "3 + 12"


def test_main_setup_incomplete_banner_uses_warning() -> None:
    """Setup-incomplete banner uses ``--color-warning`` per Frontend §3.1.4."""

    props = main.setup_incomplete_banner_props()
    assert props["color_var"] == "--color-warning"
    assert props["cta_label"] == "Open Settings"


# ---------------------------------------------------------------------------
# wizard_project
# ---------------------------------------------------------------------------


def test_wizard_project_seven_steps() -> None:
    """Frontend §4 mandates seven steps in a fixed order."""

    assert len(wizard_project.PROJECT_WIZARD_STEPS) == 7
    assert wizard_project.PROJECT_WIZARD_STEPS[0] == "lims_project"
    assert wizard_project.PROJECT_WIZARD_STEPS[-1] == "confirm"


def test_wizard_project_can_advance_blocks_until_lims_picked() -> None:
    """Step 1 requires a LIMS short_id."""

    state = wizard_project.ProjectWizardState(active_step="lims_project")
    assert wizard_project.can_advance(state) is False
    state.selected_lims_short_id = "PROJ-1"
    assert wizard_project.can_advance(state) is True


def test_wizard_project_can_advance_blocks_until_template_picked() -> None:
    state = wizard_project.ProjectWizardState(active_step="template")
    assert wizard_project.can_advance(state) is False
    state.selected_template = "lab-default-microscopy"
    assert wizard_project.can_advance(state) is True


def test_wizard_project_readme_requires_core_fields() -> None:
    state = wizard_project.ProjectWizardState(active_step="readme")
    assert wizard_project.can_advance(state) is False
    state.readme_fields = {"label": "x", "operator": "alex", "objective": "y"}
    assert wizard_project.can_advance(state) is True


def test_wizard_project_preview_blocks_with_validator_findings() -> None:
    state = wizard_project.ProjectWizardState(
        active_step="preview",
        validator_findings=[{"rule": "Placeholder", "matched": "<x>"}],
    )
    assert wizard_project.preview_step_clear(state) is False


def test_wizard_project_preview_blocks_with_low_disk() -> None:
    """Pre-flight: less than 100 MB free aborts (Frontend §10.5.4)."""

    state = wizard_project.ProjectWizardState(
        active_step="preview",
        free_disk_bytes=50 * 1024 * 1024,
    )
    assert wizard_project.preview_step_clear(state) is False
    msg = wizard_project.disk_space_pre_flight_message(state)
    assert msg is not None and "Insufficient" in msg


def test_wizard_project_preview_blocks_when_plugin_host_unhealthy() -> None:
    state = wizard_project.ProjectWizardState(
        active_step="preview",
        plugin_host_ok=False,
    )
    assert wizard_project.preview_step_clear(state) is False


def test_wizard_project_preview_passes_when_clear() -> None:
    state = wizard_project.ProjectWizardState(
        active_step="preview",
        free_disk_bytes=200 * 1024 * 1024,
        plugin_host_ok=True,
    )
    assert wizard_project.preview_step_clear(state) is True


# ---------------------------------------------------------------------------
# wizard_run
# ---------------------------------------------------------------------------


def test_wizard_run_six_steps() -> None:
    assert len(wizard_run.RUN_WIZARD_STEPS) == 6


def test_wizard_run_test_mode_title() -> None:
    state = wizard_run.RunWizardState(run_kind="test")
    assert wizard_run.title_text(state) == "New Test Run"


def test_wizard_run_experimental_mode_title() -> None:
    state = wizard_run.RunWizardState(run_kind="experimental")
    assert "Experimental" in wizard_run.title_text(state)


def test_wizard_run_test_button_uses_warning_color() -> None:
    """Test-mode primary button uses ``warning`` color (Frontend §5.3)."""

    state = wizard_run.RunWizardState(run_kind="test")
    assert wizard_run.primary_button_color(state) == "warning"
    assert wizard_run.primary_button_label(state) == "Create test run"


def test_wizard_run_experimental_button_uses_primary_color() -> None:
    state = wizard_run.RunWizardState(run_kind="experimental")
    assert wizard_run.primary_button_color(state) == "primary"
    assert wizard_run.primary_button_label(state) == "Create run"


def test_wizard_run_preview_test_path_highlights() -> None:
    """Test runs add a TestRuns/ folder and TestRun_ leaf prefix."""

    state = wizard_run.RunWizardState(
        run_kind="test",
        selected_equipment="CONFOCAL_01",
        selected_project_name="Cortex Q3 Pilot",
    )
    segments = wizard_run.preview_path_segments(state, run_date="2026-05-07")
    assert "TestRuns" in segments["segments"]
    assert any(s.startswith("TestRun_") for s in segments["segments"])
    assert segments["warning_indices"]


def test_wizard_run_preview_experimental_no_test_marker() -> None:
    state = wizard_run.RunWizardState(
        run_kind="experimental",
        selected_equipment="CONFOCAL_01",
        selected_project_name="Cortex Q3 Pilot",
    )
    segments = wizard_run.preview_path_segments(state, run_date="2026-05-07")
    assert "TestRuns" not in segments["segments"]
    assert segments["warning_indices"] == ()


def test_wizard_run_can_advance_blocks_until_project_and_equipment() -> None:
    state = wizard_run.RunWizardState(run_kind="experimental", active_step="project_equipment")
    assert wizard_run.can_advance(state) is False
    state.selected_project_name = "Cortex Q3 Pilot"
    state.selected_equipment = "CONFOCAL_01"
    assert wizard_run.can_advance(state) is True


def test_wizard_run_readme_blocks_until_core_fields() -> None:
    state = wizard_run.RunWizardState(run_kind="experimental", active_step="readme")
    assert wizard_run.can_advance(state) is False
    state.readme_fields = {"label": "x", "operator": "alex", "objective": "y"}
    assert wizard_run.can_advance(state) is True


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------


def test_settings_nine_sections() -> None:
    """Settings has nine sections (Frontend §7.2)."""

    assert len(settings.SETTINGS_SECTIONS) == 9
    assert settings.SETTINGS_SECTIONS[0] == "paths"
    assert settings.SETTINGS_SECTIONS[-1] == "application"


def test_settings_first_incomplete_returns_canonical_first() -> None:
    """First incomplete section is the first per canonical order."""

    first = settings.first_incomplete_section(("equipment", "lims"))
    assert first == "lims"


def test_settings_save_button_label_setup_incomplete() -> None:
    state = settings.SettingsState(incomplete_sections=("paths",))
    assert settings.save_button_label(state) == "Save and continue"


def test_settings_save_button_label_no_changes() -> None:
    state = settings.SettingsState(pending_change_count=0)
    assert settings.save_button_label(state) == "Save all changes"


def test_settings_save_button_label_with_count_badge() -> None:
    state = settings.SettingsState(pending_change_count=3)
    assert settings.save_button_label(state) == "Save all (3 changes)"


def test_settings_section_warning_only_for_incomplete() -> None:
    state = settings.SettingsState(incomplete_sections=("equipment",))
    assert settings.section_has_warning(state, "equipment") is True
    assert settings.section_has_warning(state, "paths") is False


# ---------------------------------------------------------------------------
# problems
# ---------------------------------------------------------------------------


def _sample_findings() -> list[problems.Finding]:
    return [
        problems.Finding(
            finding_id="f1",
            severity="hard",
            rule_class="Placeholder",
            path="Run_<run_date>",
            matched_token="<run_date>",
            run_label="Cal sweep",
            equipment="CONFOCAL_01",
            detected_at="2026-05-07T08:00:00",
            state="Active",
        ),
        problems.Finding(
            finding_id="f2",
            severity="soft",
            rule_class="Missing field",
            path="readme/operator",
            matched_token="operator",
            run_label="Cal sweep",
            equipment="CONFOCAL_01",
            detected_at="2026-05-07T08:00:00",
            state="Active",
        ),
    ]


def test_problems_filter_default_shows_hard_only() -> None:
    """Hard chip default-on; Soft default-off."""

    state = problems.ProblemsPageState()
    visible = problems.filter_findings(_sample_findings(), state)
    assert len(visible) == 1
    assert visible[0].severity == "hard"


def test_problems_filter_show_soft_when_chip_on() -> None:
    state = problems.ProblemsPageState()
    state.severity_chips = problems.filter_chips.toggle(state.severity_chips, "soft")
    visible = problems.filter_findings(_sample_findings(), state)
    assert len(visible) == 2


def test_problems_filter_search_path_substring() -> None:
    state = problems.ProblemsPageState(search="run_date")
    visible = problems.filter_findings(_sample_findings(), state)
    assert all("run_date" in f.path.lower() for f in visible)


def test_problems_filter_scope_filters_by_equipment() -> None:
    state = problems.ProblemsPageState(scope="OTHER_EQUIP")
    visible = problems.filter_findings(_sample_findings(), state)
    assert visible == []


def test_problems_override_reason_min_length() -> None:
    """Frontend §11.5: minimum 10 chars after trim."""

    ok, msg = problems.validate_override_reason("short")
    assert ok is False
    assert msg is not None and "at least" in msg


def test_problems_override_reason_max_length() -> None:
    """Frontend §11.5: maximum 500 chars after trim."""

    reason = "x" * 501
    ok, msg = problems.validate_override_reason(reason)
    assert ok is False
    assert msg is not None and "at most" in msg


def test_problems_override_reason_accepts_valid() -> None:
    ok, msg = problems.validate_override_reason("Approved by PI")
    assert ok is True
    assert msg is None


def test_problems_override_reason_trims_whitespace() -> None:
    """Whitespace is trimmed before length check."""

    ok, _ = problems.validate_override_reason("    short    ")
    assert ok is False


def test_problems_near_limit_flag() -> None:
    """Within 10 of the maximum -> near-limit warning."""

    assert problems.near_limit("x" * (problems.OVERRIDE_REASON_MAX - 9))
    assert not problems.near_limit("x" * (problems.OVERRIDE_REASON_MAX - 50))


def test_problems_empty_state_default() -> None:
    state = problems.ProblemsPageState()
    assert "No active problems" in problems.empty_state_text(state)


def test_problems_empty_state_with_hidden_soft_count() -> None:
    state = problems.ProblemsPageState()
    text = problems.empty_state_text(state, soft_findings_hidden_count=12)
    assert "soft-tier findings hidden" in text


def test_problems_empty_state_with_active_filter() -> None:
    state = problems.ProblemsPageState(search="abc")
    text = problems.empty_state_text(state)
    assert "match the current filters" in text


def test_problems_chip_definitions_match_spec() -> None:
    """Five problem-class chips per Backend §8.1.1-§8.1.5."""

    chips = problems.class_chip_definitions()
    ids = [c.chip_id for c in chips]
    assert ids == [
        "Placeholder",
        "Illegal char",
        "Mode mismatch",
        "Orphan",
        "Missing field",
    ]
