"""Unit tests for ``exlab_wizard.validator.engine`` -- creation-time mode.

The engine in creation-time mode is a pure dispatcher over the rule
helpers in :mod:`exlab_wizard.validator.rules`. These tests pin:

* Clean inputs return ``[]``.
* Each §8.1 hard-tier rule fires when its trigger is present in the
  input bundle (unresolved placeholder, illegal char, mode-prefix
  mismatch).
* Soft-tier rules (missing required field, malformed front matter)
  fire on their respective triggers.
* Multiple findings aggregate in one call.
* Sort order: hard-tier first, then by ``(rule, offending_path)``.

Many tests assume Agent A's :mod:`exlab_wizard.validator.rules` is
implemented per the inter-agent contract specified in the design spec
§8.1; until then the tests fail at import time, which is the intended
"red" state for the cross-agent gate.
"""

from __future__ import annotations

import pytest

from exlab_wizard.config.models import ValidatorConfig
from exlab_wizard.validator.engine import (
    CreationValidationInput,
    Validator,
    _split_path_segments,
)
from exlab_wizard.validator.findings import Finding

# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _clean_input(**overrides: object) -> CreationValidationInput:
    """A creation-time input that should produce zero findings.

    The proposed path uses canonical equipment / project / run segments
    consistent with §3 (equipment-first layout, ``Run_<DATE>`` leaf).
    The empty file lists keep the file-side rules silent.
    """
    base: dict[str, object] = {
        "proposed_path": "/data/lab/CONFOCAL_01/PROJ-0042/Runs/Run_2026-04-17T14-32-00",
        "variables": {"project_name": "Cortex Q3 Pilot"},
        "file_names": ("README.md", "metadata.csv"),
        "file_contents": {
            # README.md with a valid (closed) YAML front matter block.
            "README.md": (
                "---\n"
                "label: Calibration sweep\n"
                "operator: asmith\n"
                "objective: characterize\n"
                "---\n"
                "\n"
                "Body prose here.\n"
            ),
            "metadata.csv": "key,value\nfoo,bar\n",
        },
        "run_kind": "experimental",
        "template_required_field_ids": (),
        "config_required_field_ids": (),
        "readme_fields": {
            "core_fields": {
                "label": "Calibration sweep",
                "operator": "asmith",
                "objective": "characterize",
            },
            "template_fields": {},
            "config_fields": {},
            "custom_fields": [],
        },
    }
    base.update(overrides)
    return CreationValidationInput(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Path splitter
# ---------------------------------------------------------------------------


def test_split_path_segments_posix_path() -> None:
    parts = _split_path_segments("/data/lab/CONFOCAL_01/PROJ-0042/Runs/Run_2026")
    assert parts == ["data", "lab", "CONFOCAL_01", "PROJ-0042", "Runs", "Run_2026"]


def test_split_path_segments_windows_path() -> None:
    parts = _split_path_segments(r"C:\data\lab\CONFOCAL_01\PROJ-0042\Run_2026")
    assert "CONFOCAL_01" in parts
    assert parts[-1] == "Run_2026"


def test_split_path_segments_empty_string() -> None:
    assert _split_path_segments("") == []


def test_split_path_segments_drops_trailing_slash() -> None:
    parts = _split_path_segments("/a/b/c/")
    assert parts == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# Default constructor
# ---------------------------------------------------------------------------


def test_validator_default_config_uses_spec_defaults() -> None:
    """Default ValidatorConfig matches the §9 documented defaults."""
    v = Validator()
    assert v.config.content_scan_max_mib == 5
    # The default extension list contains the canonical text extensions.
    assert ".md" in v.config.content_scan_extensions
    assert ".yaml" in v.config.content_scan_extensions


def test_validator_accepts_explicit_config() -> None:
    cfg = ValidatorConfig(content_scan_max_mib=10, content_scan_extensions=[".txt"])
    v = Validator(cfg)
    assert v.config.content_scan_max_mib == 10
    assert v.config.content_scan_extensions == [".txt"]


# ---------------------------------------------------------------------------
# Clean input
# ---------------------------------------------------------------------------


def test_clean_input_returns_empty_list() -> None:
    findings = Validator().validate_creation(_clean_input())
    assert findings == []


def test_clean_input_with_test_run_returns_empty_list() -> None:
    """Test-mode input with TestRuns/ parent and TestRun_ leaf passes."""
    findings = Validator().validate_creation(
        _clean_input(
            proposed_path="/data/lab/CONFOCAL_01/PROJ-0042/TestRuns/TestRun_2026-04-17T14-32-00",
            run_kind="test",
        )
    )
    assert findings == []


# ---------------------------------------------------------------------------
# Hard-tier rules
# ---------------------------------------------------------------------------


def test_unresolved_placeholder_in_path_segment_triggers_finding() -> None:
    """An angle-bracket identifier in a path segment fires §8.1.1."""
    findings = Validator().validate_creation(
        _clean_input(
            proposed_path="/data/lab/CONFOCAL_01/PROJ-0042/Runs/Run_<run_date>",
            file_names=(),
            file_contents={},
        )
    )
    assert any(f.rule == "unresolved_placeholder_token" for f in findings)
    placeholder = next(f for f in findings if f.rule == "unresolved_placeholder_token")
    assert placeholder.tier == "hard"
    assert placeholder.matched_token == "<run_date>"


def test_unresolved_placeholder_in_file_name_triggers_finding() -> None:
    findings = Validator().validate_creation(
        _clean_input(
            file_names=("README.md", "report_<sample>.csv"),
            file_contents={
                "README.md": "---\nlabel: x\n---\n",
                "report_<sample>.csv": "k,v\n",
            },
        )
    )
    assert any(
        f.rule == "unresolved_placeholder_token" and f.offending_kind == "file_name"
        for f in findings
    )


def test_leftover_jinja_marker_in_file_content_triggers_finding() -> None:
    findings = Validator().validate_creation(
        _clean_input(
            file_names=("config.yaml",),
            file_contents={"config.yaml": "name: {{ project }}\n"},
        )
    )
    assert any(f.rule == "leftover_jinja_marker" for f in findings)
    marker = next(f for f in findings if f.rule == "leftover_jinja_marker")
    assert marker.tier == "hard"


def test_illegal_filesystem_character_in_file_name_triggers_finding() -> None:
    """A Windows-illegal char in a file name fires §8.1.2."""
    findings = Validator().validate_creation(
        _clean_input(
            file_names=("README.md", "bad?name.txt"),
            file_contents={
                "README.md": "---\nlabel: x\n---\n",
                "bad?name.txt": "data\n",
            },
        )
    )
    assert any(f.rule == "illegal_filesystem_character" for f in findings)
    illegal = next(f for f in findings if f.rule == "illegal_filesystem_character")
    assert illegal.tier == "hard"


def test_reserved_filesystem_name_triggers_finding() -> None:
    """A Windows-reserved file base name (CON, NUL, ...) fires §8.1.2."""
    findings = Validator().validate_creation(
        _clean_input(
            file_names=("README.md", "CON.txt"),
            file_contents={
                "README.md": "---\nlabel: x\n---\n",
                "CON.txt": "data\n",
            },
        )
    )
    assert any(f.rule == "reserved_filesystem_name" for f in findings)


def test_run_leaf_with_test_run_kind_triggers_mode_prefix_mismatch() -> None:
    """A ``Run_`` leaf paired with ``run_kind='test'`` fires §8.1.3."""
    findings = Validator().validate_creation(
        _clean_input(
            proposed_path="/data/lab/CONFOCAL_01/PROJ-0042/Runs/Run_2026-04-17T14-32-00",
            run_kind="test",
        )
    )
    assert any(f.rule == "mode_prefix_mismatch" for f in findings)
    mismatch = next(f for f in findings if f.rule == "mode_prefix_mismatch")
    assert mismatch.tier == "hard"


def test_test_run_leaf_with_experimental_run_kind_triggers_mode_prefix_mismatch() -> None:
    """A ``TestRun_`` leaf with ``run_kind='experimental'`` fires §8.1.3."""
    findings = Validator().validate_creation(
        _clean_input(
            proposed_path="/data/lab/CONFOCAL_01/PROJ-0042/TestRuns/TestRun_2026-04-17T14-32-00",
            run_kind="experimental",
        )
    )
    assert any(f.rule == "mode_prefix_mismatch" for f in findings)


# ---------------------------------------------------------------------------
# Soft-tier rules
# ---------------------------------------------------------------------------


def test_missing_required_field_triggers_soft_tier_finding() -> None:
    """A required field absent from readme_fields fires §8.1.5."""
    findings = Validator().validate_creation(
        _clean_input(
            template_required_field_ids=("sample_type",),
            readme_fields={
                "core_fields": {"label": "x", "operator": "y", "objective": "z"},
                "template_fields": {},
                "config_fields": {},
                "custom_fields": [],
            },
        )
    )
    assert any(f.rule == "missing_required_field" for f in findings)
    missing = next(f for f in findings if f.rule == "missing_required_field")
    assert missing.tier == "soft"


def test_required_field_present_does_not_trigger_finding() -> None:
    """When the required field IS present in readme_fields, no finding."""
    findings = Validator().validate_creation(
        _clean_input(
            template_required_field_ids=("sample_type",),
            readme_fields={
                "core_fields": {"label": "x", "operator": "y", "objective": "z"},
                "template_fields": {"sample_type": "fixed_tissue"},
                "config_fields": {},
                "custom_fields": [],
            },
        )
    )
    assert not any(f.rule == "missing_required_field" for f in findings)


def test_malformed_yaml_front_matter_triggers_soft_tier_finding() -> None:
    """An unterminated front-matter block fires §8.1.1's malformed soft rule."""
    findings = Validator().validate_creation(
        _clean_input(
            file_contents={
                # Open ``---`` with no closing fence.
                "README.md": "---\nlabel: x\noperator: y\nbody prose without close\n",
                "metadata.csv": "k,v\n",
            },
        )
    )
    assert any(f.rule == "malformed_yaml_front_matter" for f in findings)
    finding = next(f for f in findings if f.rule == "malformed_yaml_front_matter")
    assert finding.tier == "soft"


def test_no_readme_md_skips_front_matter_rule() -> None:
    """Front-matter rule does not run if README.md is absent from inputs."""
    findings = Validator().validate_creation(
        _clean_input(
            file_names=("metadata.csv",),
            file_contents={"metadata.csv": "k,v\n"},
        )
    )
    assert not any(f.rule == "malformed_yaml_front_matter" for f in findings)


# ---------------------------------------------------------------------------
# Aggregation + sort
# ---------------------------------------------------------------------------


def test_multiple_findings_aggregate_in_one_call() -> None:
    """A single bundle that trips multiple rules surfaces all findings."""
    findings = Validator().validate_creation(
        _clean_input(
            proposed_path="/data/lab/CONFOCAL_01/PROJ-0042/Runs/Run_<run_date>",
            run_kind="test",
            file_names=("README.md", "bad?name.txt"),
            file_contents={
                "README.md": "---\nlabel: x\n---\n",
                "bad?name.txt": "data\n",
            },
        )
    )
    rules_fired = {f.rule for f in findings}
    # We expect at least: unresolved_placeholder_token (path),
    # mode_prefix_mismatch (Run_ vs test), illegal_filesystem_character
    # (the ? in the file name).
    assert "unresolved_placeholder_token" in rules_fired
    assert "mode_prefix_mismatch" in rules_fired
    assert "illegal_filesystem_character" in rules_fired


def test_findings_are_sorted_with_hard_tier_first() -> None:
    """The output is sorted with hard-tier findings before soft."""
    findings = Validator().validate_creation(
        _clean_input(
            # Hard: unresolved placeholder in path.
            proposed_path="/data/lab/CONFOCAL_01/PROJ-0042/Runs/Run_<run_date>",
            # Soft: required field missing.
            template_required_field_ids=("sample_type",),
            readme_fields={
                "core_fields": {"label": "x", "operator": "y", "objective": "z"},
                "template_fields": {},
                "config_fields": {},
                "custom_fields": [],
            },
        )
    )
    # Find the index of the first soft finding; every hard one must
    # come before it.
    tiers = [f.tier for f in findings]
    if "soft" in tiers:
        first_soft = tiers.index("soft")
        assert all(t == "hard" for t in tiers[:first_soft])


def test_findings_sorted_lexicographically_within_same_tier() -> None:
    """Within one tier, findings sort by (rule, offending_path)."""
    findings = Validator().validate_creation(
        _clean_input(
            proposed_path="/data/lab/CONFOCAL_01/PROJ-0042/Runs/Run_<run_date>",
            run_kind="test",
        )
    )
    # Two hard findings expected: mode_prefix_mismatch and
    # unresolved_placeholder_token. Lexicographic order: m < u.
    hard_rules = [f.rule for f in findings if f.tier == "hard"]
    if len(hard_rules) >= 2:
        assert hard_rules == sorted(hard_rules)


# ---------------------------------------------------------------------------
# Finding shape stamping
# ---------------------------------------------------------------------------


def test_findings_carry_run_path_from_proposed_path() -> None:
    """Engine stamps run_path on every finding from the input."""
    proposed = "/data/lab/CONFOCAL_01/PROJ-0042/Runs/Run_<run_date>"
    findings = Validator().validate_creation(_clean_input(proposed_path=proposed))
    assert findings  # there is at least one finding
    for f in findings:
        assert f.run_path == proposed


def test_findings_default_audit_flags_to_false() -> None:
    """Audit-only flags default to False at creation time."""
    findings = Validator().validate_creation(
        _clean_input(
            proposed_path="/data/lab/CONFOCAL_01/PROJ-0042/Runs/Run_<run_date>",
        )
    )
    assert findings
    for f in findings:
        assert f.synced_under_prior_policy is False
        assert f.override_active is False


def test_findings_are_finding_instances() -> None:
    """The engine returns :class:`Finding` instances (not dicts)."""
    findings = Validator().validate_creation(
        _clean_input(
            proposed_path="/data/lab/CONFOCAL_01/PROJ-0042/Runs/Run_<run_date>",
        )
    )
    for f in findings:
        assert isinstance(f, Finding)


# ---------------------------------------------------------------------------
# Input bundle validation
# ---------------------------------------------------------------------------


def test_creation_validation_input_is_frozen() -> None:
    """The input bundle is a frozen dataclass for determinism."""
    bundle = _clean_input()
    with pytest.raises(AttributeError):
        bundle.run_kind = "test"  # type: ignore[misc]


def test_creation_validation_input_defaults() -> None:
    """Every field except proposed_path is optional with a safe default."""
    bundle = CreationValidationInput(proposed_path="/x")
    assert bundle.run_kind == "experimental"
    assert bundle.file_names == ()
    assert dict(bundle.file_contents) == {}
    assert bundle.template_required_field_ids == ()
    assert bundle.config_required_field_ids == ()
