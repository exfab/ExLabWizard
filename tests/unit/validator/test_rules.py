"""Tests for ``exlab_wizard.validator.rules``. Backend Spec §8.1.

Covers each §8.1 rule function with positive (rule fires) and negative
(rule passes) cases. Each rule has its own subsection in §8.1, so the
tests are grouped likewise.
"""

from __future__ import annotations

import pytest

from exlab_wizard.validator.rules import (
    check_illegal_filesystem_character,
    check_malformed_yaml_front_matter,
    check_missing_required_field,
    check_mode_prefix_mismatch,
    check_orphan,
    check_reserved_filesystem_name,
    check_unresolved_placeholder,
    check_unsafe_project_name,
)

# ---------------------------------------------------------------------------
# §8.1.1 Unresolved-placeholder rule
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "segment,expected_token",
    [
        ("Run_<run_date>", "<run_date>"),
        ("<random>", "<random>"),
        ("<project>", "<project>"),
        ("<_underscore_start>", "<_underscore_start>"),
    ],
)
def test_angle_bracket_token_in_segment_fires(segment: str, expected_token: str) -> None:
    findings = check_unresolved_placeholder(
        path_segments=[segment],
        file_names=[],
        file_contents={},
    )
    assert len(findings) == 1
    assert findings[0]["rule"] == "unresolved_placeholder_token"
    assert findings[0]["tier"] == "hard"
    assert findings[0]["matched_token"] == expected_token
    assert findings[0]["offending_kind"] == "directory_segment"
    assert findings[0]["offending_path"] == segment


@pytest.mark.parametrize(
    "segment",
    [
        "<foo bar>",  # space disallowed in identifier grammar
        "<2 mM>",  # chemistry notation
        "<>",  # empty
        "<-not-id>",  # leading dash, not letter/underscore
        "<123>",  # leading digit
        r"\\smb\share",  # SMB path
        "Run_2024-01-01T10-00-00",  # legitimate run name
        "PROJ-0001",  # legitimate project
    ],
)
def test_non_token_angle_brackets_pass(segment: str) -> None:
    findings = check_unresolved_placeholder(
        path_segments=[segment],
        file_names=[],
        file_contents={},
    )
    # No angle-bracket findings; Jinja patterns also should not fire on these.
    placeholder_findings = [f for f in findings if f["rule"] == "unresolved_placeholder_token"]
    assert placeholder_findings == []


def test_jinja_var_marker_in_filename_fires() -> None:
    findings = check_unresolved_placeholder(
        path_segments=[],
        file_names=["{{var}}.txt"],
        file_contents={},
    )
    assert any(f["rule"] == "leftover_jinja_marker" for f in findings)
    finding = next(f for f in findings if f["rule"] == "leftover_jinja_marker")
    assert finding["tier"] == "hard"
    assert finding["matched_token"] == "{{var}}"
    assert finding["offending_kind"] == "file_name"


def test_jinja_block_marker_in_content_fires() -> None:
    findings = check_unresolved_placeholder(
        path_segments=[],
        file_names=[],
        file_contents={"foo.md": "before {% if x %} after"},
    )
    assert any(f["rule"] == "leftover_jinja_marker" for f in findings)
    finding = next(f for f in findings if f["rule"] == "leftover_jinja_marker")
    assert finding["matched_token"] == "{% if x %}"
    assert finding["offending_kind"] == "file_content"
    assert finding["offending_path"] == "foo.md"


def test_unresolved_placeholder_in_content_fires() -> None:
    findings = check_unresolved_placeholder(
        path_segments=[],
        file_names=[],
        file_contents={"readme.md": "see <project> here"},
    )
    assert any(f["rule"] == "unresolved_placeholder_token" for f in findings)


def test_no_findings_on_clean_inputs() -> None:
    findings = check_unresolved_placeholder(
        path_segments=["CONFOCAL_01", "PROJ-0042", "Run_2024-01-01T10-00-00"],
        file_names=["README.md", "readme_fields.json"],
        file_contents={"README.md": "# Hello\n\nThis is fine."},
    )
    assert findings == []


def test_large_text_file_skipped_from_content_scan() -> None:
    # Build content >5 MiB containing a placeholder token; rule should NOT
    # fire because the file is over the size cap.
    big_content = "a" * (5 * 1024 * 1024 + 1) + "<placeholder>"
    findings = check_unresolved_placeholder(
        path_segments=[],
        file_names=[],
        file_contents={"big.txt": big_content},
    )
    assert findings == []


def test_under_size_cap_content_is_scanned() -> None:
    # Just under the 5 MiB cap -- placeholder should be detected.
    content = "x" * (1024) + "<token>"
    findings = check_unresolved_placeholder(
        path_segments=[],
        file_names=[],
        file_contents={"small.txt": content},
    )
    assert any(f["matched_token"] == "<token>" for f in findings)


def test_multiple_matches_in_same_input_each_yield_finding() -> None:
    findings = check_unresolved_placeholder(
        path_segments=["<a>_<b>_<c>"],
        file_names=[],
        file_contents={},
    )
    tokens = sorted(f["matched_token"] for f in findings)
    assert tokens == ["<a>", "<b>", "<c>"]


def test_empty_file_contents_dict_is_path_only_scan() -> None:
    findings = check_unresolved_placeholder(
        path_segments=["<x>"],
        file_names=[],
        file_contents={},
    )
    assert len(findings) == 1


# ---------------------------------------------------------------------------
# §8.1.2 Illegal-filesystem-character rule
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,expected_char",
    [
        ("name<test>", "<"),
        ("name>test", ">"),
        ("name:test", ":"),
        ('name"test', '"'),
        ("name|test", "|"),
        ("name?test", "?"),
        ("name*test", "*"),
        ("name\\test", "\\"),
        ("name/test", "/"),
    ],
)
def test_windows_illegal_char_in_segment_fires(name: str, expected_char: str) -> None:
    findings = check_illegal_filesystem_character(
        path_segments=[name],
        file_names=[],
    )
    assert any(
        f["rule"] == "illegal_filesystem_character" and f["matched_token"] == expected_char
        for f in findings
    )


def test_nul_byte_in_name_fires() -> None:
    name = "name\x00test"
    findings = check_illegal_filesystem_character(
        path_segments=[],
        file_names=[name],
    )
    assert any(f["matched_token"] == "\x00" for f in findings)


def test_low_ascii_control_char_fires() -> None:
    # ASCII 0x07 (BEL) is in the 0-31 range
    name = "abc\x07def"
    findings = check_illegal_filesystem_character(
        path_segments=[],
        file_names=[name],
    )
    assert any(f["rule"] == "illegal_filesystem_character" for f in findings)


def test_trailing_dot_fires() -> None:
    findings = check_illegal_filesystem_character(
        path_segments=["myname."],
        file_names=[],
    )
    assert any(f["matched_token"] == "." for f in findings)


def test_trailing_space_fires() -> None:
    findings = check_illegal_filesystem_character(
        path_segments=["myname "],
        file_names=[],
    )
    assert any(f["matched_token"] == " " for f in findings)


@pytest.mark.parametrize(
    "name",
    [
        "Run_2024-01-01T10-00-00",
        "PROJ-0001",
        "CONFOCAL_01",
        "readme.md",
        "name_with-dashes.txt",
        "deep_subdir",
    ],
)
def test_normal_names_pass(name: str) -> None:
    findings = check_illegal_filesystem_character(
        path_segments=[name],
        file_names=[name],
    )
    assert findings == []


def test_findings_have_hard_tier() -> None:
    findings = check_illegal_filesystem_character(
        path_segments=["bad<name"],
        file_names=[],
    )
    assert all(f["tier"] == "hard" for f in findings)


# ---------------------------------------------------------------------------
# §8.1.2 Reserved-filesystem-name rule
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "CON",
        "con",  # case-insensitive
        "Con",
        "PRN",
        "AUX",
        "NUL",
        "COM1",
        "COM9",
        "LPT1",
        "LPT9",
        "CON.txt",  # with extension
        "PRN.log",
        "lpt9.dat",  # lowercase with extension
    ],
)
def test_reserved_name_fires(name: str) -> None:
    findings = check_reserved_filesystem_name(file_names=[name])
    assert len(findings) == 1
    assert findings[0]["rule"] == "reserved_filesystem_name"
    assert findings[0]["tier"] == "hard"


@pytest.mark.parametrize(
    "name",
    [
        "myfile.txt",
        "CONsole.txt",  # CON is the prefix but not the stem
        "CON_extra",  # underscore makes it not a reserved stem
        "CONS",  # not in the reserved set
        "COM0",  # 0 not in 1..9
        "COM10",  # 10 not in 1..9
        "LPT0",
        "README.md",
        "data",
    ],
)
def test_non_reserved_name_passes(name: str) -> None:
    findings = check_reserved_filesystem_name(file_names=[name])
    assert findings == []


# ---------------------------------------------------------------------------
# §3.2 Unsafe-project-name rule
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "bad/name",
        "back\\slash",
        " leading",
        "trailing ",
        "trailing.",
        "café",  # non-ASCII
        "CON",  # reserved Windows device name
        'quote"',
    ],
)
def test_unsafe_project_name_fires(name: str) -> None:
    findings = check_unsafe_project_name(name=name)
    assert findings
    assert all(f["rule"] == "unsafe_project_name" for f in findings)
    # Audit mode is advisory: a non-conforming directory already on disk
    # is surfaced for review, not blocked.
    assert all(f["tier"] == "soft" for f in findings)
    assert all(f["offending_kind"] == "directory_segment" for f in findings)
    assert all(f["offending_path"] == name for f in findings)


@pytest.mark.parametrize(
    "name",
    [
        "Cortex Q3 Pilot",  # spaces are fine -- used verbatim
        "PROJ-0042",
        "Q3_run (pilot)",
        "a.b.c",
    ],
)
def test_safe_project_name_passes(name: str) -> None:
    assert check_unsafe_project_name(name=name) == []


# ---------------------------------------------------------------------------
# §8.1.3 Mode-prefix mismatch rule
# ---------------------------------------------------------------------------


def test_experimental_with_run_prefix_under_runs_parent_passes() -> None:
    """Redesign §3.4: experimental run leaf parented by ``Runs`` is valid."""
    findings = check_mode_prefix_mismatch(
        leaf_dir_name="Run_2024-01-01T10-00-00",
        parent_dir_name="Runs",
        creation_run_kind="experimental",
    )
    assert findings == []


def test_experimental_with_run_prefix_at_project_level_fires() -> None:
    """Redesign §3.4: a misplaced ``Run_*`` directly under the project (not
    under ``Runs/``) is now a hard mode_prefix_mismatch."""
    findings = check_mode_prefix_mismatch(
        leaf_dir_name="Run_2024-01-01T10-00-00",
        parent_dir_name="PROJ-0001",
        creation_run_kind="experimental",
    )
    assert len(findings) == 1
    assert findings[0]["rule"] == "mode_prefix_mismatch"
    assert findings[0]["matched_token"] == "PROJ-0001"


def test_experimental_with_test_run_prefix_fires() -> None:
    findings = check_mode_prefix_mismatch(
        leaf_dir_name="TestRun_2024-01-01T10-00-00",
        parent_dir_name="PROJ-0001",
        creation_run_kind="experimental",
    )
    assert any(f["rule"] == "mode_prefix_mismatch" for f in findings)
    assert all(f["tier"] == "hard" for f in findings)


def test_experimental_under_test_runs_parent_fires() -> None:
    findings = check_mode_prefix_mismatch(
        leaf_dir_name="Run_2024-01-01T10-00-00",
        parent_dir_name="TestRuns",
        creation_run_kind="experimental",
    )
    assert any(f["rule"] == "mode_prefix_mismatch" for f in findings)


def test_test_with_test_run_prefix_under_test_runs_passes() -> None:
    findings = check_mode_prefix_mismatch(
        leaf_dir_name="TestRun_2024-01-01T10-00-00",
        parent_dir_name="TestRuns",
        creation_run_kind="test",
    )
    assert findings == []


def test_test_with_run_prefix_fires() -> None:
    findings = check_mode_prefix_mismatch(
        leaf_dir_name="Run_2024-01-01T10-00-00",
        parent_dir_name="TestRuns",
        creation_run_kind="test",
    )
    assert any(f["rule"] == "mode_prefix_mismatch" for f in findings)


def test_test_with_test_run_prefix_but_wrong_parent_fires() -> None:
    findings = check_mode_prefix_mismatch(
        leaf_dir_name="TestRun_2024-01-01T10-00-00",
        parent_dir_name="PROJ-0001",
        creation_run_kind="test",
    )
    assert any(f["rule"] == "mode_prefix_mismatch" for f in findings)


def test_run_kind_none_returns_no_findings() -> None:
    findings = check_mode_prefix_mismatch(
        leaf_dir_name="Run_unknown",
        parent_dir_name=None,
        creation_run_kind=None,
    )
    assert findings == []


# ---------------------------------------------------------------------------
# §8.1.4 Orphan rule
# ---------------------------------------------------------------------------


def test_project_without_creation_json_fires() -> None:
    findings = check_orphan(level="project", has_creation_json=False)
    assert len(findings) == 1
    assert findings[0]["rule"] == "orphan"
    assert findings[0]["tier"] == "soft"


def test_run_without_creation_json_fires() -> None:
    findings = check_orphan(level="run", has_creation_json=False)
    assert len(findings) == 1
    assert findings[0]["rule"] == "orphan"


def test_equipment_without_creation_json_does_not_fire() -> None:
    findings = check_orphan(level="equipment", has_creation_json=False)
    assert findings == []


def test_project_with_creation_json_passes() -> None:
    findings = check_orphan(level="project", has_creation_json=True)
    assert findings == []


def test_run_with_creation_json_passes() -> None:
    findings = check_orphan(level="run", has_creation_json=True)
    assert findings == []


# ---------------------------------------------------------------------------
# §8.1.5 Missing-required-field rule
# ---------------------------------------------------------------------------


def test_missing_id_fires() -> None:
    findings = check_missing_required_field(
        readme_fields={
            "core_fields": {"objective": "do science"},
            "template_fields": {},
            "config_fields": {},
        },
        required_field_ids=["objective", "operator"],
    )
    assert len(findings) == 1
    assert findings[0]["matched_token"] == "operator"
    assert findings[0]["tier"] == "soft"


def test_empty_string_value_fires() -> None:
    findings = check_missing_required_field(
        readme_fields={
            "core_fields": {"objective": ""},
            "template_fields": {},
            "config_fields": {},
        },
        required_field_ids=["objective"],
    )
    assert len(findings) == 1
    assert findings[0]["matched_token"] == "objective"


def test_present_id_with_non_empty_value_passes() -> None:
    findings = check_missing_required_field(
        readme_fields={
            "core_fields": {"objective": "study X"},
            "template_fields": {"protocol": "v3"},
            "config_fields": {"site": "lab1"},
        },
        required_field_ids=["objective", "protocol", "site"],
    )
    assert findings == []


def test_field_present_in_template_fields_layer_passes() -> None:
    findings = check_missing_required_field(
        readme_fields={
            "core_fields": {},
            "template_fields": {"protocol": "v3"},
            "config_fields": {},
        },
        required_field_ids=["protocol"],
    )
    assert findings == []


def test_none_readme_fields_treats_all_required_as_missing() -> None:
    findings = check_missing_required_field(
        readme_fields=None,
        required_field_ids=["objective"],
    )
    assert len(findings) == 1


def test_value_explicit_none_fires() -> None:
    findings = check_missing_required_field(
        readme_fields={
            "core_fields": {"objective": None},
            "template_fields": {},
            "config_fields": {},
        },
        required_field_ids=["objective"],
    )
    assert len(findings) == 1


# ---------------------------------------------------------------------------
# §8.1 Malformed YAML front matter rule
# ---------------------------------------------------------------------------


def test_well_formed_front_matter_passes() -> None:
    content = "---\nkey: value\n---\n\n# Body\n"
    assert check_malformed_yaml_front_matter(content=content) == []


def test_unterminated_front_matter_fires() -> None:
    content = "---\nkey: value\n"
    findings = check_malformed_yaml_front_matter(content=content)
    assert len(findings) == 1
    assert findings[0]["rule"] == "malformed_yaml_front_matter"
    assert findings[0]["tier"] == "soft"


def test_malformed_yaml_in_block_fires() -> None:
    # YAML with an invalid mapping (key starts with ":")
    content = "---\n: bad\n---\n"
    findings = check_malformed_yaml_front_matter(content=content)
    assert len(findings) == 1
    assert findings[0]["rule"] == "malformed_yaml_front_matter"


def test_no_front_matter_passes() -> None:
    content = "# Just prose\n\nNo front matter.\n"
    assert check_malformed_yaml_front_matter(content=content) == []


def test_empty_content_passes() -> None:
    assert check_malformed_yaml_front_matter(content="") == []


def test_front_matter_with_nested_yaml_passes() -> None:
    content = (
        "---\n"
        "title: My Run\n"
        "tags:\n"
        "  - a\n"
        "  - b\n"
        "metadata:\n"
        "  operator: alice\n"
        "  date: 2024-01-01\n"
        "---\n"
        "\n"
        "# Body\n"
    )
    assert check_malformed_yaml_front_matter(content=content) == []
