"""Verify the regex literals plus filename-rule constants.

Each regex is exercised through both acceptance and rejection examples so a
silent grammar change shows up as a failing test rather than a runtime
validator regression.
"""

from __future__ import annotations

import re

from exlab_wizard.constants import patterns


# ---------------------------------------------------------------------------
# Equipment ID -- Backend Spec §3.1
# ---------------------------------------------------------------------------


def test_equipment_id_regex_literal() -> None:
    assert patterns.EQUIPMENT_ID_REGEX == r"^[A-Z][A-Z0-9_]*$"


def test_equipment_id_pattern_is_compiled() -> None:
    assert isinstance(patterns.EQUIPMENT_ID_PATTERN, re.Pattern)
    assert patterns.EQUIPMENT_ID_PATTERN.pattern == patterns.EQUIPMENT_ID_REGEX


def test_equipment_id_pattern_accepts_valid_ids() -> None:
    accepted = [
        "A",
        "CONFOCAL_01",
        "MICROSCOPE",
        "XRD_LAB_2",
        "Z9",
        "FOO_BAR_BAZ",
    ]
    for value in accepted:
        assert patterns.EQUIPMENT_ID_PATTERN.match(value), value


def test_equipment_id_pattern_rejects_invalid_ids() -> None:
    rejected = [
        "",                # empty
        "1FOO",            # starts with a digit
        "_FOO",            # starts with underscore
        "foo",             # lowercase
        "FOO BAR",         # whitespace
        "FOO-BAR",         # hyphen
        "FOO.BAR",         # dot
        "FOO/BAR",         # slash
    ]
    for value in rejected:
        assert not patterns.EQUIPMENT_ID_PATTERN.match(value), value


def test_equipment_id_max_length() -> None:
    # Backend Spec §3.1.
    assert patterns.EQUIPMENT_ID_MAX_LENGTH == 32


# ---------------------------------------------------------------------------
# Placeholder regexes -- Backend Spec §8.1.1
# ---------------------------------------------------------------------------


def test_placeholder_angle_bracket_regex_literal() -> None:
    assert patterns.PLACEHOLDER_ANGLE_BRACKET_REGEX == r"<[A-Za-z_][A-Za-z0-9_]*>"


def test_placeholder_angle_bracket_pattern_finds_tokens() -> None:
    text = "Hello <name>, please contact <user_2> regarding <Project> today."
    found = patterns.PLACEHOLDER_ANGLE_BRACKET_PATTERN.findall(text)
    assert found == ["<name>", "<user_2>", "<Project>"]


def test_placeholder_angle_bracket_pattern_ignores_non_tokens() -> None:
    # Pure HTML-style tags with attributes or digits-led content do not match.
    rejected_substrings = ["<1foo>", "< name>", "<>", "<-bad>"]
    for value in rejected_substrings:
        assert not patterns.PLACEHOLDER_ANGLE_BRACKET_PATTERN.search(value), value


def test_placeholder_jinja_var_regex_literal() -> None:
    assert patterns.PLACEHOLDER_JINJA_VAR_REGEX == r"\{\{[^}]*\}\}"


def test_placeholder_jinja_var_pattern_finds_markers() -> None:
    text = "header {{ project.name }} and {{value}} done."
    found = patterns.PLACEHOLDER_JINJA_VAR_PATTERN.findall(text)
    assert found == ["{{ project.name }}", "{{value}}"]


def test_placeholder_jinja_var_pattern_ignores_plain_text() -> None:
    assert not patterns.PLACEHOLDER_JINJA_VAR_PATTERN.search("nothing here")
    assert not patterns.PLACEHOLDER_JINJA_VAR_PATTERN.search("{ single }")


def test_placeholder_jinja_block_regex_literal() -> None:
    assert patterns.PLACEHOLDER_JINJA_BLOCK_REGEX == r"\{%[^%]*%\}"


def test_placeholder_jinja_block_pattern_finds_blocks() -> None:
    text = "before {% if x %} middle {% endif %} after"
    found = patterns.PLACEHOLDER_JINJA_BLOCK_PATTERN.findall(text)
    assert found == ["{% if x %}", "{% endif %}"]


def test_placeholder_jinja_block_pattern_ignores_unrelated_content() -> None:
    assert not patterns.PLACEHOLDER_JINJA_BLOCK_PATTERN.search("100% sure")


# ---------------------------------------------------------------------------
# Project short ID -- Backend Spec §7.2
# ---------------------------------------------------------------------------


def test_project_short_id_regex_literal() -> None:
    assert patterns.PROJECT_SHORT_ID_REGEX == r"^PROJ-\d+$"


def test_project_short_id_pattern_accepts() -> None:
    for value in ("PROJ-1", "PROJ-42", "PROJ-1000000"):
        assert patterns.PROJECT_SHORT_ID_PATTERN.match(value), value


def test_project_short_id_pattern_rejects() -> None:
    rejected = ["proj-1", "PROJ_1", "PROJ-", "PROJ-12a", "PROJ-1 ", " PROJ-1"]
    for value in rejected:
        assert not patterns.PROJECT_SHORT_ID_PATTERN.match(value), value


# ---------------------------------------------------------------------------
# Template question ID -- Backend Spec §5
# ---------------------------------------------------------------------------


def test_template_question_id_regex_literal() -> None:
    assert patterns.TEMPLATE_QUESTION_ID_REGEX == r"^[a-z][a-z0-9_]*$"


def test_template_question_id_pattern_accepts() -> None:
    for value in ("a", "objective", "objective_v2", "step_1_data"):
        assert patterns.TEMPLATE_QUESTION_ID_PATTERN.match(value), value


def test_template_question_id_pattern_rejects() -> None:
    for value in ("Objective", "1step", "_objective", "step-1", ""):
        assert not patterns.TEMPLATE_QUESTION_ID_PATTERN.match(value), value


# ---------------------------------------------------------------------------
# Plugin name -- Backend Spec §6
# ---------------------------------------------------------------------------


def test_plugin_name_regex_literal() -> None:
    assert patterns.PLUGIN_NAME_REGEX == r"^[A-Za-z0-9_-]+$"


def test_plugin_name_pattern_accepts() -> None:
    for value in ("plugin", "Plugin", "plugin-1", "plugin_1", "ABC", "1"):
        assert patterns.PLUGIN_NAME_PATTERN.match(value), value


def test_plugin_name_pattern_rejects() -> None:
    for value in ("", "plugin name", "plugin/x", "plugin.x", "plugin!"):
        assert not patterns.PLUGIN_NAME_PATTERN.match(value), value


# ---------------------------------------------------------------------------
# Run directory naming -- Backend Spec §3
# ---------------------------------------------------------------------------


def test_run_date_strftime_literal() -> None:
    # ISO 8601 with colons replaced by hyphens for cross-platform safety.
    assert patterns.RUN_DATE_STRFTIME == "%Y-%m-%dT%H-%M-%S"


def test_run_date_strftime_round_trip() -> None:
    # Sanity: the format is parseable in both directions.
    from datetime import datetime

    sample = datetime(2026, 5, 6, 12, 34, 56)
    formatted = sample.strftime(patterns.RUN_DATE_STRFTIME)
    assert formatted == "2026-05-06T12-34-56"
    parsed = datetime.strptime(formatted, patterns.RUN_DATE_STRFTIME)
    assert parsed == sample


def test_run_dir_prefix_literal() -> None:
    assert patterns.RUN_DIR_PREFIX == "Run_"


def test_test_run_dir_prefix_literal() -> None:
    assert patterns.TEST_RUN_DIR_PREFIX == "TestRun_"


def test_test_runs_dir_name_literal() -> None:
    assert patterns.TEST_RUNS_DIR_NAME == "TestRuns"


# ---------------------------------------------------------------------------
# Windows filename rules -- Backend Spec §8.1.2
# ---------------------------------------------------------------------------


def test_windows_reserved_names_membership() -> None:
    # Spec lists CON / PRN / AUX / NUL plus COM1..COM9 plus LPT1..LPT9.
    expected = (
        {"CON", "PRN", "AUX", "NUL"}
        | {f"COM{i}" for i in range(1, 10)}
        | {f"LPT{i}" for i in range(1, 10)}
    )
    assert patterns.WINDOWS_RESERVED_NAMES == expected


def test_windows_reserved_names_is_frozenset() -> None:
    assert isinstance(patterns.WINDOWS_RESERVED_NAMES, frozenset)


def test_windows_reserved_names_size() -> None:
    # 4 + 9 + 9 = 22.
    assert len(patterns.WINDOWS_RESERVED_NAMES) == 22


def test_windows_reserved_names_are_uppercase() -> None:
    # The values themselves are uppercase; case-insensitive comparison happens
    # at the call site by upper-casing the candidate name.
    for name in patterns.WINDOWS_RESERVED_NAMES:
        assert name == name.upper(), name


def test_windows_reserved_names_case_insensitive_check() -> None:
    # Documented usage: ``candidate.upper() in WINDOWS_RESERVED_NAMES``.
    candidates = ["con", "Con", "CON", "lpt3", "Com9", "NuL"]
    for candidate in candidates:
        assert candidate.upper() in patterns.WINDOWS_RESERVED_NAMES, candidate

    # Names that are not reserved must remain non-members regardless of case.
    non_reserved = ["readme", "Run_2026-05-06T12-34-56", "COM10", "LPT0"]
    for candidate in non_reserved:
        assert candidate.upper() not in patterns.WINDOWS_RESERVED_NAMES, candidate


def test_windows_illegal_chars_literal() -> None:
    # Backend Spec §8.1.2.
    assert patterns.WINDOWS_ILLEGAL_CHARS == '<>:"/\\|?*'


def test_windows_illegal_chars_membership() -> None:
    for ch in '<>:"/\\|?*':
        assert ch in patterns.WINDOWS_ILLEGAL_CHARS, ch


def test_windows_illegal_chars_excludes_safe() -> None:
    for ch in "abcDEF_-.0123":
        assert ch not in patterns.WINDOWS_ILLEGAL_CHARS, ch


# ---------------------------------------------------------------------------
# Package re-exports
# ---------------------------------------------------------------------------


def test_patterns_re_exported_from_package() -> None:
    # Both the raw strings and the compiled patterns must be importable from
    # the top-level package.
    from exlab_wizard import constants

    assert constants.EQUIPMENT_ID_REGEX == r"^[A-Z][A-Z0-9_]*$"
    assert isinstance(constants.EQUIPMENT_ID_PATTERN, re.Pattern)
    assert constants.EQUIPMENT_ID_MAX_LENGTH == 32
    assert constants.RUN_DATE_STRFTIME == "%Y-%m-%dT%H-%M-%S"
    assert constants.RUN_DIR_PREFIX == "Run_"
    assert constants.TEST_RUN_DIR_PREFIX == "TestRun_"
    assert constants.TEST_RUNS_DIR_NAME == "TestRuns"
    assert constants.WINDOWS_ILLEGAL_CHARS == '<>:"/\\|?*'
    assert "CON" in constants.WINDOWS_RESERVED_NAMES
