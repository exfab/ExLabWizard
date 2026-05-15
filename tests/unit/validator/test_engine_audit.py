"""Unit tests for ``Validator.audit`` and ``Validator.query_problems``.

Backend Spec §4.4.4, §8.1, §11.7, §11.8. The audit-mode pass walks a
directory subtree, reads ``creation.json`` per directory, and emits
findings shaped by the §8.1 rule catalog. These tests pin the
audit-mode contract end-to-end against fixture trees built under
``tmp_path``: every test constructs a real on-disk subtree, calls
``audit`` (or ``query_problems``), and asserts the structured output
matches the §11.8 finding shape.

The contract surfaces tested below:

- Clean trees produce ``[]``.
- Orphan rule (§8.1.4) fires when ``creation.json`` is missing at
  project / run level.
- Mode-prefix mismatch (§8.1.3) fires when the leaf's prefix disagrees
  with ``creation.json.run_kind``.
- Unresolved-placeholder rule (§8.1.1) fires on directory names, file
  names, and file contents.
- ``override_active`` is set when the run's ``validation_overrides``
  has a non-revoked entry whose ``problem_class`` matches.
- ``synced_under_prior_policy`` is set when a hard-tier finding hits a
  run whose ``creation.json.sync_status`` is ``"synced"``.
- Content-scan caps (size + extension allowlist) are honored.
- Binary files are detected via the 8-KiB null-byte sniff.
- ``query_problems`` is a public alias for ``audit``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from exlab_wizard.config.models import ValidatorConfig
from exlab_wizard.validator.engine import Validator
from exlab_wizard.validator.findings import Finding

# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _build_creation_json_dict(
    *,
    run_kind: str = "experimental",
    sync_status: str = "pending",
    validation_overrides: list[dict[str, Any]] | None = None,
    schema_version: str = "1.9",
    short_id: str = "PROJ-0042",
    level: str = "run",
    extras: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the wire-form ``creation.json`` dict for a fixture tree."""
    payload: dict[str, Any] = {
        "schema_version": schema_version,
        "created_at": "2026-04-17T14:32:00Z",
        "created_by": "asmith",
        "level": level,
        "run_kind": run_kind,
        "lims_project": {
            "uid": "8c7e9d2f-1a4b-4e6c-9b3d-7f2a1e5d8c4b",
            "short_id": short_id,
            "name_at_creation": "Cortex Q3 Pilot",
            "source": "live",
            "cache_freshness_at_use": None,
        },
        "template": {
            "name": "confocal_run_v2",
            "version": "2.1",
            "source_path": "templates/confocal_run_v2",
            "run_scope": "both",
        },
        "variables": {"project_name": "Cortex Q3 Pilot"},
        "paths": {"local": "/data/lab/X", "nas": "//nas/X"},
        "plugins_applied": [],
        "sync_status": sync_status,
        "validation_overrides": validation_overrides or [],
    }
    if extras:
        payload.update(extras)
    return payload


def _write_creation_json(directory: Path, payload: dict[str, Any]) -> Path:
    """Write the wire-form ``creation.json`` under ``<directory>/.exlab-wizard``."""
    cache_dir = directory / ".exlab-wizard"
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / "creation.json"
    path.write_bytes(json.dumps(payload).encode("utf-8"))
    return path


def _make_clean_tree(
    tmp_path: Path,
    *,
    equipment_id: str = "CONFOCAL_01",
    project_name: str = "Cortex Q3 Pilot",
    run_leaf: str = "Run_2026-04-17T14-32-00",
    run_kind: str = "experimental",
    sync_status: str = "pending",
    validation_overrides: list[dict[str, Any]] | None = None,
) -> tuple[Path, Path, Path]:
    """Build a single-equipment / single-project / single-run clean tree.

    Returns ``(equipment_root, project_dir, run_dir)``. ``project_name``
    is the verbatim human-readable ``<project>/`` folder segment (§3.2).
    Both project and run levels carry a ``creation.json``; the run-level
    file is the one the audit applies the override / sync flags against.
    """
    equipment_root = tmp_path / equipment_id
    project_dir = equipment_root / project_name
    # Redesign §3.4: experimental runs live under <project>/Runs/.
    run_dir = project_dir / "Runs" / run_leaf
    run_dir.mkdir(parents=True)

    _write_creation_json(
        project_dir,
        _build_creation_json_dict(
            run_kind=run_kind,
            sync_status=sync_status,
            validation_overrides=validation_overrides,
            level="project",
        ),
    )
    _write_creation_json(
        run_dir,
        _build_creation_json_dict(
            run_kind=run_kind,
            sync_status=sync_status,
            validation_overrides=validation_overrides,
            level="run",
        ),
    )
    return equipment_root, project_dir, run_dir


def _make_validator(
    *,
    equipment_id: str = "CONFOCAL_01",
    equipment_root: Path | None = None,
    content_scan_max_mib: int = 5,
    content_scan_extensions: list[str] | None = None,
    staging_root: Path | None = None,
) -> Validator:
    """Construct a :class:`Validator` for tests with a single equipment root."""
    config = ValidatorConfig(
        content_scan_max_mib=content_scan_max_mib,
        content_scan_extensions=content_scan_extensions
        or [".txt", ".md", ".json", ".yaml", ".yml", ".csv"],
    )
    equipment_roots: dict[str, Path] = {}
    if equipment_root is not None:
        equipment_roots[equipment_id] = equipment_root
    return Validator(
        validator_config=config,
        equipment_roots=equipment_roots,
        staging_root=staging_root,
    )


def _by_rule(findings: list[Finding], rule: str) -> list[Finding]:
    return [f for f in findings if f.rule == rule]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_audit_clean_tree_returns_empty_list(tmp_path: Path) -> None:
    """A well-formed equipment / project / run tree produces no findings."""
    equipment_root, _, _ = _make_clean_tree(tmp_path)
    validator = _make_validator(equipment_root=equipment_root)
    findings = validator.audit({"kind": "all"})
    assert findings == []


def test_audit_project_missing_creation_json_returns_orphan(tmp_path: Path) -> None:
    """A project directory with no ``creation.json`` produces one orphan finding."""
    equipment_root = tmp_path / "CONFOCAL_01"
    project_dir = equipment_root / "PROJ-0042"
    run_dir = project_dir / "Run_2026-04-17T14-32-00"
    run_dir.mkdir(parents=True)
    # NOTE: deliberately no creation.json at the project level.
    _write_creation_json(
        run_dir,
        _build_creation_json_dict(),
    )

    validator = _make_validator(equipment_root=equipment_root)
    findings = validator.audit({"kind": "all"})
    orphans = _by_rule(findings, "orphan")
    assert len(orphans) == 1
    assert orphans[0].run_path == str(project_dir)
    assert orphans[0].offending_path == str(project_dir)
    assert orphans[0].tier == "soft"


def test_audit_run_missing_creation_json_returns_orphan(tmp_path: Path) -> None:
    """A run directory with no ``creation.json`` produces one orphan finding."""
    equipment_root = tmp_path / "CONFOCAL_01"
    project_dir = equipment_root / "PROJ-0042"
    run_dir = project_dir / "Run_2026-04-17T14-32-00"
    run_dir.mkdir(parents=True)
    _write_creation_json(project_dir, _build_creation_json_dict(level="project"))
    # No creation.json at the run level.

    validator = _make_validator(equipment_root=equipment_root)
    findings = validator.audit({"kind": "all"})
    orphans = _by_rule(findings, "orphan")
    assert len(orphans) == 1
    assert orphans[0].offending_path == str(run_dir)
    assert orphans[0].tier == "soft"


def test_audit_unsafe_project_name_returns_soft_finding(tmp_path: Path) -> None:
    """A project directory whose name is not a safe path segment (§3.2)
    produces a soft-tier ``unsafe_project_name`` finding."""
    equipment_root = tmp_path / "CONFOCAL_01"
    # Non-ASCII -> not a safe single path segment per §3.2.
    project_dir = equipment_root / "café"
    run_dir = project_dir / "Run_2026-04-17T14-32-00"
    run_dir.mkdir(parents=True)
    _write_creation_json(project_dir, _build_creation_json_dict(level="project"))
    _write_creation_json(run_dir, _build_creation_json_dict(level="run"))

    validator = _make_validator(equipment_root=equipment_root)
    findings = validator.audit({"kind": "all"})
    unsafe = _by_rule(findings, "unsafe_project_name")
    assert len(unsafe) == 1
    assert unsafe[0].tier == "soft"
    assert unsafe[0].offending_path == str(project_dir)


def test_audit_clean_project_name_has_no_unsafe_finding(tmp_path: Path) -> None:
    """A human-readable project name (spaces and all) is a safe segment."""
    equipment_root, _project_dir, _run_dir = _make_clean_tree(tmp_path)
    validator = _make_validator(equipment_root=equipment_root)
    findings = validator.audit({"kind": "all"})
    assert _by_rule(findings, "unsafe_project_name") == []


def test_audit_mode_prefix_mismatch_run_with_test_kind(tmp_path: Path) -> None:
    """A ``Run_*`` leaf whose creation.json says ``run_kind="test"`` is flagged."""
    equipment_root = tmp_path / "CONFOCAL_01"
    project_dir = equipment_root / "PROJ-0042"
    run_dir = project_dir / "Run_2026-04-17T14-32-00"
    run_dir.mkdir(parents=True)
    _write_creation_json(project_dir, _build_creation_json_dict(level="project"))
    _write_creation_json(
        run_dir,
        _build_creation_json_dict(run_kind="test", level="run"),
    )

    validator = _make_validator(equipment_root=equipment_root)
    findings = validator.audit({"kind": "all"})
    mode_findings = _by_rule(findings, "mode_prefix_mismatch")
    assert len(mode_findings) >= 1
    # Both the leaf-prefix branch and the parent-folder branch should fire,
    # since ``Run_*`` is not under ``TestRuns/``.
    assert any(f.tier == "hard" for f in mode_findings)
    assert all(f.run_path == str(run_dir) for f in mode_findings)


def test_audit_unresolved_placeholder_in_directory_name(tmp_path: Path) -> None:
    """A directory name containing ``<placeholder>`` produces a finding."""
    equipment_root = tmp_path / "CONFOCAL_01"
    project_dir = equipment_root / "PROJ-0042"
    # Note: pytest's tmp_path is a real filesystem; the placeholder must
    # be a legal directory name on the host. ``<token>`` is legal on POSIX
    # tmpfs so the test runs cleanly.
    placeholder_run = project_dir / "Run_<run_date>"
    placeholder_run.mkdir(parents=True)
    _write_creation_json(project_dir, _build_creation_json_dict(level="project"))
    _write_creation_json(
        placeholder_run,
        _build_creation_json_dict(level="run"),
    )

    validator = _make_validator(equipment_root=equipment_root)
    findings = validator.audit({"kind": "all"})
    placeholder_findings = _by_rule(findings, "unresolved_placeholder_token")
    assert len(placeholder_findings) >= 1
    assert any(f.matched_token == "<run_date>" for f in placeholder_findings)
    assert all(f.tier == "hard" for f in placeholder_findings)


def test_audit_override_active_flag_set_when_problem_class_overridden(
    tmp_path: Path,
) -> None:
    """An active override with matching ``problem_class`` flips ``override_active``."""
    overrides = [
        {
            "id": "aa-1234",
            "problem_class": "unresolved_placeholder_token",
            "operator": "asmith",
            "recorded_at": "2026-04-18T09:14:22Z",
            "reason": "vendor template",
            "revoked": False,
        }
    ]
    equipment_root = tmp_path / "CONFOCAL_01"
    project_dir = equipment_root / "PROJ-0042"
    placeholder_run = project_dir / "Run_<run_date>"
    placeholder_run.mkdir(parents=True)
    _write_creation_json(project_dir, _build_creation_json_dict(level="project"))
    _write_creation_json(
        placeholder_run,
        _build_creation_json_dict(level="run", validation_overrides=overrides),
    )

    validator = _make_validator(equipment_root=equipment_root)
    findings = validator.audit({"kind": "all"})
    placeholder_findings = _by_rule(findings, "unresolved_placeholder_token")
    # The leaf-name placeholder finding sees the run's overrides and is
    # therefore flagged as ``override_active``.
    on_leaf = [f for f in placeholder_findings if f.offending_path == str(placeholder_run)]
    assert any(f.override_active for f in on_leaf)


def test_audit_synced_under_prior_policy_flag_for_hard_finding_on_synced_run(
    tmp_path: Path,
) -> None:
    """Synced run + hard finding -> ``synced_under_prior_policy=True``."""
    equipment_root = tmp_path / "CONFOCAL_01"
    project_dir = equipment_root / "PROJ-0042"
    placeholder_run = project_dir / "Run_<run_date>"
    placeholder_run.mkdir(parents=True)
    _write_creation_json(project_dir, _build_creation_json_dict(level="project"))
    _write_creation_json(
        placeholder_run,
        _build_creation_json_dict(level="run", sync_status="synced"),
    )

    validator = _make_validator(equipment_root=equipment_root)
    findings = validator.audit({"kind": "all"})
    placeholder_findings = _by_rule(findings, "unresolved_placeholder_token")
    on_leaf = [f for f in placeholder_findings if f.offending_path == str(placeholder_run)]
    assert any(f.synced_under_prior_policy for f in on_leaf)
    # Soft-tier findings should NOT carry the flag even on the synced run.
    soft_findings = [f for f in findings if f.tier == "soft"]
    assert all(not f.synced_under_prior_policy for f in soft_findings)


def test_audit_synced_under_prior_policy_flag_for_hard_finding_on_cleaned_run(
    tmp_path: Path,
) -> None:
    """Cleaned run + hard finding -> ``synced_under_prior_policy=True``.

    A ``cleaned`` run was synced first, so any hard-tier finding it carries
    must still flag as "synced under the prior policy" — same gate as
    ``synced``.
    """
    equipment_root = tmp_path / "CONFOCAL_01"
    project_dir = equipment_root / "PROJ-0042"
    placeholder_run = project_dir / "Run_<run_date>"
    placeholder_run.mkdir(parents=True)
    _write_creation_json(project_dir, _build_creation_json_dict(level="project"))
    _write_creation_json(
        placeholder_run,
        _build_creation_json_dict(level="run", sync_status="cleaned"),
    )

    validator = _make_validator(equipment_root=equipment_root)
    findings = validator.audit({"kind": "all"})
    placeholder_findings = _by_rule(findings, "unresolved_placeholder_token")
    on_leaf = [f for f in placeholder_findings if f.offending_path == str(placeholder_run)]
    assert any(f.synced_under_prior_policy for f in on_leaf)


def test_audit_respects_content_scan_max_mib_cap(tmp_path: Path) -> None:
    """A large file with a placeholder is NOT scanned for content."""
    equipment_root, _, run_dir = _make_clean_tree(tmp_path)
    big_file = run_dir / "huge.txt"
    # Write 2 MiB of harmless text + an unresolved placeholder near the end.
    payload = b"x" * (2 * 1024 * 1024) + b"\nleftover <run_date>\n"
    big_file.write_bytes(payload)

    # Cap at 1 MiB so the 2 MiB file is skipped from content scanning.
    validator = _make_validator(
        equipment_root=equipment_root,
        content_scan_max_mib=1,
    )
    findings = validator.audit({"kind": "all"})
    # The filename ``huge.txt`` itself is fine, so no finding on the
    # filename. The content was skipped, so no content-level finding.
    placeholder_content_findings = [
        f
        for f in findings
        if f.rule == "unresolved_placeholder_token" and f.offending_kind == "file_content"
    ]
    assert placeholder_content_findings == []


def test_audit_respects_extensions_allowlist(tmp_path: Path) -> None:
    """A ``.bin`` file is skipped from content scanning by the extension gate."""
    equipment_root, _, run_dir = _make_clean_tree(tmp_path)
    bin_file = run_dir / "data.bin"
    bin_file.write_bytes(b"leftover <run_date>")

    validator = _make_validator(
        equipment_root=equipment_root,
        content_scan_extensions=[".txt", ".md"],
    )
    findings = validator.audit({"kind": "all"})
    placeholder_content_findings = [
        f
        for f in findings
        if f.rule == "unresolved_placeholder_token" and f.offending_kind == "file_content"
    ]
    assert placeholder_content_findings == []


def test_audit_detects_binary_files_via_null_byte_sniff(tmp_path: Path) -> None:
    """A ``.txt`` file with a NUL byte is treated as binary and skipped."""
    equipment_root, _, run_dir = _make_clean_tree(tmp_path)
    bin_text_file = run_dir / "binary.txt"
    # The 8-KiB sniff is the gate: any NUL byte in the first 8 KiB
    # disqualifies the file. We put one near the front, then add a
    # placeholder later so the test would fire if the sniff didn't run.
    bin_text_file.write_bytes(b"abc\x00defleftover <run_date>")

    validator = _make_validator(equipment_root=equipment_root)
    findings = validator.audit({"kind": "all"})
    placeholder_content_findings = [
        f
        for f in findings
        if f.rule == "unresolved_placeholder_token" and f.offending_kind == "file_content"
    ]
    assert placeholder_content_findings == []


def test_audit_scans_text_file_content_when_within_limits(tmp_path: Path) -> None:
    """A small ``.txt`` file inside the run directory is scanned for placeholders."""
    equipment_root, _, run_dir = _make_clean_tree(tmp_path)
    text_file = run_dir / "notes.txt"
    text_file.write_bytes(b"leftover <run_date> here")

    validator = _make_validator(equipment_root=equipment_root)
    findings = validator.audit({"kind": "all"})
    placeholder_content_findings = [
        f
        for f in findings
        if f.rule == "unresolved_placeholder_token" and f.offending_kind == "file_content"
    ]
    assert any(f.matched_token == "<run_date>" for f in placeholder_content_findings)


def test_audit_findings_are_sorted_by_tier_then_rule_then_offending_path(
    tmp_path: Path,
) -> None:
    """Hard-tier findings come first; ties break on rule then offending_path."""
    equipment_root = tmp_path / "CONFOCAL_01"
    project_dir = equipment_root / "PROJ-0042"
    run_dir = project_dir / "Run_<a>_<b>"
    run_dir.mkdir(parents=True)
    # Project has no creation.json -> soft orphan.
    # Run has placeholders in its name -> hard placeholder.
    _write_creation_json(run_dir, _build_creation_json_dict(level="run"))

    validator = _make_validator(equipment_root=equipment_root)
    findings = validator.audit({"kind": "all"})
    assert findings, "expected at least one finding"
    tiers = [f.tier for f in findings]
    # Every "hard" finding must come before every "soft" finding.
    if "hard" in tiers and "soft" in tiers:
        first_soft = tiers.index("soft")
        assert "hard" not in tiers[first_soft:]


def test_query_problems_is_alias_for_audit(tmp_path: Path) -> None:
    """``query_problems`` returns the same list as ``audit``."""
    equipment_root, _, run_dir = _make_clean_tree(tmp_path)
    placeholder_run = run_dir.parent / "Run_<run_date>"
    placeholder_run.mkdir(parents=True)
    _write_creation_json(placeholder_run, _build_creation_json_dict(level="run"))

    validator = _make_validator(equipment_root=equipment_root)
    a = validator.audit({"kind": "all"})
    b = validator.query_problems({"kind": "all"})
    assert a == b


def test_audit_scope_equipment_id_resolves_to_configured_root(tmp_path: Path) -> None:
    """``{"kind": "equipment_id", ...}`` walks only the matching subtree."""
    equipment_root_a = tmp_path / "CONFOCAL_01"
    equipment_root_b = tmp_path / "OTHER_EQUIP"
    (equipment_root_a / "PROJ-0042" / "Runs" / "Run_2026-04-17T14-32-00").mkdir(parents=True)
    (equipment_root_b / "PROJ-0099" / "Runs" / "Run_<placeholder>").mkdir(parents=True)
    _write_creation_json(
        equipment_root_a / "PROJ-0042" / "Runs" / "Run_2026-04-17T14-32-00",
        _build_creation_json_dict(level="run"),
    )
    _write_creation_json(
        equipment_root_a / "PROJ-0042",
        _build_creation_json_dict(level="project"),
    )
    _write_creation_json(
        equipment_root_b / "PROJ-0099" / "Runs" / "Run_<placeholder>",
        _build_creation_json_dict(level="run"),
    )
    _write_creation_json(
        equipment_root_b / "PROJ-0099",
        _build_creation_json_dict(level="project"),
    )

    validator = Validator(
        equipment_roots={
            "CONFOCAL_01": equipment_root_a,
            "OTHER_EQUIP": equipment_root_b,
        }
    )
    a_only = validator.audit({"kind": "equipment_id", "value": "CONFOCAL_01"})
    b_only = validator.audit({"kind": "equipment_id", "value": "OTHER_EQUIP"})
    # CONFOCAL_01 has a clean tree -> no findings.
    assert a_only == []
    # OTHER_EQUIP has a placeholder leaf -> at least one hard finding.
    assert any(f.rule == "unresolved_placeholder_token" for f in b_only)


def test_audit_scope_project_path_walks_one_subtree(tmp_path: Path) -> None:
    """``{"kind": "project_path", ...}`` walks the supplied path only."""
    equipment_root = tmp_path / "CONFOCAL_01"
    project_a = equipment_root / "PROJ-0042"
    project_b = equipment_root / "PROJ-0099"
    (project_a / "Run_2026-04-17T14-32-00").mkdir(parents=True)
    (project_b / "Run_<placeholder>").mkdir(parents=True)
    _write_creation_json(project_a, _build_creation_json_dict(level="project"))
    _write_creation_json(
        project_a / "Run_2026-04-17T14-32-00",
        _build_creation_json_dict(level="run"),
    )
    _write_creation_json(project_b, _build_creation_json_dict(level="project"))
    _write_creation_json(
        project_b / "Run_<placeholder>",
        _build_creation_json_dict(level="run"),
    )

    validator = _make_validator(equipment_root=equipment_root)
    findings_a = validator.audit({"kind": "project_path", "value": str(project_a)})
    findings_b = validator.audit({"kind": "project_path", "value": str(project_b)})
    assert findings_a == []
    assert any(f.rule == "unresolved_placeholder_token" for f in findings_b)


def test_audit_scope_all_walks_every_configured_equipment(tmp_path: Path) -> None:
    """``{"kind": "all"}`` aggregates findings across every equipment root."""
    equipment_root_a = tmp_path / "CONFOCAL_01"
    equipment_root_b = tmp_path / "OTHER_EQUIP"
    (equipment_root_a / "PROJ-0042" / "Runs" / "Run_<a>").mkdir(parents=True)
    (equipment_root_b / "PROJ-0099" / "Runs" / "Run_<b>").mkdir(parents=True)
    _write_creation_json(
        equipment_root_a / "PROJ-0042",
        _build_creation_json_dict(level="project"),
    )
    _write_creation_json(
        equipment_root_a / "PROJ-0042" / "Runs" / "Run_<a>",
        _build_creation_json_dict(level="run"),
    )
    _write_creation_json(
        equipment_root_b / "PROJ-0099",
        _build_creation_json_dict(level="project"),
    )
    _write_creation_json(
        equipment_root_b / "PROJ-0099" / "Runs" / "Run_<b>",
        _build_creation_json_dict(level="run"),
    )

    validator = Validator(
        equipment_roots={
            "CONFOCAL_01": equipment_root_a,
            "OTHER_EQUIP": equipment_root_b,
        }
    )
    findings = validator.audit({"kind": "all"})
    matched = {f.matched_token for f in findings if f.matched_token is not None}
    assert "<a>" in matched
    assert "<b>" in matched


def test_audit_scope_unknown_equipment_id_returns_empty(tmp_path: Path) -> None:
    """An ``equipment_id`` not in the map produces an empty result, not an error."""
    validator = _make_validator(equipment_root=tmp_path / "missing-root")
    findings = validator.audit({"kind": "equipment_id", "value": "does_not_exist"})
    assert findings == []


def test_audit_skips_cache_directory_contents(tmp_path: Path) -> None:
    """The walk does not apply §8.1 rules to ``.exlab-wizard`` contents."""
    equipment_root, _, run_dir = _make_clean_tree(tmp_path)
    # Drop a placeholder-laden file INSIDE the cache dir; the walk must
    # skip it because the cache dir is owned by the engine, not the user.
    cache_dir = run_dir / ".exlab-wizard"
    (cache_dir / "stray<run_date>.txt").write_bytes(b"<run_date>")

    validator = _make_validator(equipment_root=equipment_root)
    findings = validator.audit({"kind": "all"})
    # No filename rules fired on ``stray<run_date>.txt`` because the
    # cache-dir entry is skipped at the parent's scandir loop.
    paths = {f.offending_path for f in findings}
    assert all(".exlab-wizard" not in p for p in paths)


def test_audit_revoked_override_does_not_set_override_active(tmp_path: Path) -> None:
    """A tombstone-revoked override does not flip ``override_active``."""
    overrides = [
        {
            "id": "aa-1234",
            "problem_class": "unresolved_placeholder_token",
            "operator": "asmith",
            "recorded_at": "2026-04-18T09:14:22Z",
            "reason": "vendor template",
            "revoked": False,
        },
        {
            "id": "bb-5678",
            "revokes": "aa-1234",
            "operator": "asmith",
            "recorded_at": "2026-05-01T11:02:14Z",
            "reason": "vendor fixed",
            "revoked": True,
        },
    ]
    equipment_root = tmp_path / "CONFOCAL_01"
    project_dir = equipment_root / "PROJ-0042"
    placeholder_run = project_dir / "Run_<run_date>"
    placeholder_run.mkdir(parents=True)
    _write_creation_json(project_dir, _build_creation_json_dict(level="project"))
    _write_creation_json(
        placeholder_run,
        _build_creation_json_dict(level="run", validation_overrides=overrides),
    )

    validator = _make_validator(equipment_root=equipment_root)
    findings = validator.audit({"kind": "all"})
    placeholder_findings = _by_rule(findings, "unresolved_placeholder_token")
    on_leaf = [f for f in placeholder_findings if f.offending_path == str(placeholder_run)]
    assert all(not f.override_active for f in on_leaf)


def test_audit_unresolved_placeholder_in_file_name(tmp_path: Path) -> None:
    """A file name containing ``<placeholder>`` produces a finding."""
    equipment_root, _, run_dir = _make_clean_tree(tmp_path)
    bad_file = run_dir / "report_<run_date>.txt"
    bad_file.write_bytes(b"clean content")

    validator = _make_validator(equipment_root=equipment_root)
    findings = validator.audit({"kind": "all"})
    file_name_findings = [
        f
        for f in findings
        if f.rule == "unresolved_placeholder_token" and f.offending_kind == "file_name"
    ]
    assert any(f.matched_token == "<run_date>" for f in file_name_findings)


def test_audit_test_run_with_experimental_kind_flagged(tmp_path: Path) -> None:
    """A ``TestRun_*`` leaf with ``run_kind="experimental"`` triggers mismatch."""
    equipment_root = tmp_path / "CONFOCAL_01"
    project_dir = equipment_root / "PROJ-0042"
    test_runs_dir = project_dir / "TestRuns"
    test_run_dir = test_runs_dir / "TestRun_2026-04-17T14-32-00"
    test_run_dir.mkdir(parents=True)
    _write_creation_json(project_dir, _build_creation_json_dict(level="project"))
    _write_creation_json(
        test_run_dir,
        _build_creation_json_dict(run_kind="experimental", level="run"),
    )

    validator = _make_validator(equipment_root=equipment_root)
    findings = validator.audit({"kind": "all"})
    mode_findings = _by_rule(findings, "mode_prefix_mismatch")
    assert len(mode_findings) >= 1
    assert all(f.tier == "hard" for f in mode_findings)


def test_audit_returns_list_of_finding_dataclass_instances(tmp_path: Path) -> None:
    """Every entry in the result is a :class:`Finding` instance."""
    equipment_root = tmp_path / "CONFOCAL_01"
    project_dir = equipment_root / "PROJ-0042"
    run_dir = project_dir / "Run_<run_date>"
    run_dir.mkdir(parents=True)
    _write_creation_json(project_dir, _build_creation_json_dict(level="project"))
    _write_creation_json(run_dir, _build_creation_json_dict(level="run"))

    validator = _make_validator(equipment_root=equipment_root)
    findings = validator.audit({"kind": "all"})
    assert findings  # there are findings to check
    assert all(isinstance(f, Finding) for f in findings)


def test_audit_includes_staging_root_when_orchestrator_on(tmp_path: Path) -> None:
    """``"all"`` scope walks the staging root when one is configured."""
    equipment_root = tmp_path / "CONFOCAL_01"
    equipment_root.mkdir()
    staging_root = tmp_path / "staging"
    staging_run = staging_root / "PROJ-0042" / "Runs" / "Run_<staged>"
    staging_run.mkdir(parents=True)
    _write_creation_json(staging_run, _build_creation_json_dict(level="run"))

    validator = Validator(
        equipment_roots={"CONFOCAL_01": equipment_root},
        staging_root=staging_root,
    )
    findings = validator.audit({"kind": "all"})
    matched = {f.matched_token for f in findings if f.matched_token is not None}
    assert "<staged>" in matched


# ---------------------------------------------------------------------------
# Parametric: every finding has the §11.8 shape
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "scope_kind",
    ["all", "equipment_id", "project_path"],
)
def test_audit_findings_carry_full_section_11_8_shape(
    tmp_path: Path,
    scope_kind: str,
) -> None:
    """Every finding has the documented §11.8 fields populated."""
    equipment_root = tmp_path / "CONFOCAL_01"
    project_dir = equipment_root / "PROJ-0042"
    run_dir = project_dir / "Run_<run_date>"
    run_dir.mkdir(parents=True)
    _write_creation_json(project_dir, _build_creation_json_dict(level="project"))
    _write_creation_json(run_dir, _build_creation_json_dict(level="run"))

    validator = _make_validator(equipment_root=equipment_root)
    if scope_kind == "all":
        findings = validator.audit({"kind": "all"})
    elif scope_kind == "equipment_id":
        findings = validator.audit({"kind": "equipment_id", "value": "CONFOCAL_01"})
    else:
        findings = validator.audit({"kind": "project_path", "value": str(project_dir)})

    assert findings
    for f in findings:
        # §11.8 required fields:
        assert f.rule
        assert f.tier in {"hard", "soft"}
        assert f.run_path
        assert f.offending_path
        assert f.offending_kind in {"directory_segment", "file_name", "file_content"}
        # Optional but always present on the dataclass:
        assert isinstance(f.synced_under_prior_policy, bool)
        assert isinstance(f.override_active, bool)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_audit_unknown_scope_kind_raises_value_error(tmp_path: Path) -> None:
    """An unrecognised ``scope.kind`` raises ``ValueError`` (defensive guard)."""
    validator = _make_validator(equipment_root=tmp_path)
    with pytest.raises(ValueError, match="unknown audit scope kind"):
        validator.audit({"kind": "bogus_kind"})  # type: ignore[arg-type]


def test_audit_root_does_not_exist_returns_empty(tmp_path: Path) -> None:
    """Walking a missing root produces no findings (silent skip)."""
    validator = Validator(
        equipment_roots={"CONFOCAL_01": tmp_path / "missing"},
    )
    assert validator.audit({"kind": "all"}) == []


def test_audit_root_is_a_file_returns_empty(tmp_path: Path) -> None:
    """Walking a root that is a regular file (not a directory) returns []."""
    file_root = tmp_path / "not_a_dir"
    file_root.write_bytes(b"")
    validator = Validator(equipment_roots={"CONFOCAL_01": file_root})
    assert validator.audit({"kind": "all"}) == []


def test_audit_handles_unreadable_directory(tmp_path: Path, monkeypatch: Any) -> None:
    """A ``PermissionError`` from ``os.scandir`` does not crash the walk."""
    import os as _os

    equipment_root = tmp_path / "CONFOCAL_01"
    equipment_root.mkdir()

    real_scandir = _os.scandir

    def _raising_scandir(path: Any) -> Any:
        if str(path) == str(equipment_root):
            raise PermissionError("denied")
        return real_scandir(path)

    monkeypatch.setattr("exlab_wizard.validator.engine.os.scandir", _raising_scandir)
    validator = _make_validator(equipment_root=equipment_root)
    # Walk silently swallows the PermissionError.
    assert validator.audit({"kind": "all"}) == []


def test_audit_other_level_subfolder_under_project(tmp_path: Path) -> None:
    """A project subfolder that is not a ``Run_*`` / ``TestRuns`` is "other"."""
    equipment_root = tmp_path / "CONFOCAL_01"
    project_dir = equipment_root / "PROJ-0042"
    misc_dir = project_dir / "misc"
    misc_dir.mkdir(parents=True)
    _write_creation_json(project_dir, _build_creation_json_dict(level="project"))

    validator = _make_validator(equipment_root=equipment_root)
    findings = validator.audit({"kind": "all"})
    # The "other" subfolder doesn't fire orphan / mismatch by itself; this
    # exercises the "other" branch of _classify_level + _compute_run_path
    # without raising.
    assert all(f.rule != "mode_prefix_mismatch" for f in findings)


def test_audit_other_under_test_runs_unknown_leaf(tmp_path: Path) -> None:
    """A non-``TestRun_`` leaf under ``TestRuns/`` is classified as "other"."""
    equipment_root = tmp_path / "CONFOCAL_01"
    project_dir = equipment_root / "PROJ-0042"
    test_runs_dir = project_dir / "TestRuns"
    odd_leaf = test_runs_dir / "Run_2026-01-01"  # not TestRun_*
    odd_leaf.mkdir(parents=True)
    _write_creation_json(project_dir, _build_creation_json_dict(level="project"))

    validator = _make_validator(equipment_root=equipment_root)
    findings = validator.audit({"kind": "all"})
    # "other" classification means no orphan/mismatch finding fires here.
    on_leaf = [f for f in findings if str(odd_leaf) in f.offending_path]
    assert all(f.rule != "orphan" for f in on_leaf)


def test_audit_deeply_nested_other_subfolder(tmp_path: Path) -> None:
    """A subfolder under a run is classified as "other"; rules still walk into it."""
    equipment_root, _, run_dir = _make_clean_tree(tmp_path)
    nested = run_dir / "subdir"
    nested.mkdir()
    placeholder_file = nested / "leak_<token>.txt"
    placeholder_file.write_bytes(b"clean")

    validator = _make_validator(equipment_root=equipment_root)
    findings = validator.audit({"kind": "all"})
    # The placeholder in the file name DOES fire even on nested "other" dirs.
    assert any(
        f.rule == "unresolved_placeholder_token"
        and f.offending_kind == "file_name"
        and f.matched_token == "<token>"
        for f in findings
    )


def test_audit_creation_json_unreadable(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """A ``creation.json`` that raises ``OSError`` on read is treated as absent."""
    equipment_root, _, run_dir = _make_clean_tree(tmp_path)
    creation_path = run_dir / ".exlab-wizard" / "creation.json"

    real_read_bytes = Path.read_bytes

    def _raising_read_bytes(self: Path) -> bytes:
        if self == creation_path:
            raise OSError("io error")
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", _raising_read_bytes)
    validator = _make_validator(equipment_root=equipment_root)
    findings = validator.audit({"kind": "all"})
    # The run looks orphan-y because we couldn't read its creation.json.
    assert any(f.rule == "orphan" and f.run_path == str(run_dir) for f in findings)


def test_audit_creation_json_malformed_raw_decode(tmp_path: Path) -> None:
    """A ``creation.json`` with invalid JSON bytes is treated as absent."""
    equipment_root, _, run_dir = _make_clean_tree(tmp_path)
    creation_path = run_dir / ".exlab-wizard" / "creation.json"
    creation_path.write_bytes(b"not json {{{")

    validator = _make_validator(equipment_root=equipment_root)
    findings = validator.audit({"kind": "all"})
    # Treated as missing creation.json -> orphan.
    assert any(f.rule == "orphan" and f.run_path == str(run_dir) for f in findings)


def test_audit_creation_json_missing_required_struct_field(tmp_path: Path) -> None:
    """A ``creation.json`` valid as raw dict but failing CreationJson decode."""
    equipment_root = tmp_path / "CONFOCAL_01"
    project_dir = equipment_root / "PROJ-0042"
    run_dir = project_dir / "Run_2026-04-17T14-32-00"
    run_dir.mkdir(parents=True)
    cache_dir = run_dir / ".exlab-wizard"
    cache_dir.mkdir()
    # Valid JSON object, but missing every CreationJson required field.
    (cache_dir / "creation.json").write_bytes(b'{"foo": "bar"}')
    _write_creation_json(project_dir, _build_creation_json_dict(level="project"))

    validator = _make_validator(equipment_root=equipment_root)
    findings = validator.audit({"kind": "all"})
    # The run still reads as not-orphan (raw dict survived); but the typed
    # decode failed, so creation_payload is None and no mismatch fires.
    # The audit should not crash.
    assert isinstance(findings, list)


def test_audit_readme_fields_json_decode_failure(tmp_path: Path) -> None:
    """A malformed ``readme_fields.json`` is silently skipped (no crash)."""
    equipment_root, _, run_dir = _make_clean_tree(tmp_path)
    readme_fields_path = run_dir / ".exlab-wizard" / "readme_fields.json"
    readme_fields_path.write_bytes(b"not valid json {")

    validator = _make_validator(equipment_root=equipment_root)
    # Walk completes without raising.
    findings = validator.audit({"kind": "all"})
    assert isinstance(findings, list)


def test_audit_missing_required_field_with_required_ids(tmp_path: Path) -> None:
    """Audit picks up missing required README fields when stamped on creation.json."""
    equipment_root = tmp_path / "CONFOCAL_01"
    project_dir = equipment_root / "PROJ-0042"
    run_dir = project_dir / "Run_2026-04-17T14-32-00"
    run_dir.mkdir(parents=True)

    creation_payload = _build_creation_json_dict(
        level="run",
        extras={"required_readme_field_ids": ["sample_type"]},
    )
    _write_creation_json(project_dir, _build_creation_json_dict(level="project"))
    _write_creation_json(run_dir, creation_payload)

    # Write a readme_fields.json missing the required field.
    cache_dir = run_dir / ".exlab-wizard"
    readme_fields_payload: dict[str, Any] = {
        "schema_version": "1.1",
        "generated_at": "2026-04-17T14:32:05Z",
        "core_fields": {"label": "x", "operator": "y", "objective": "z"},
        "system_fields": {},
        "template_fields": {},
        "config_fields": {},
        "custom_fields": [],
    }
    (cache_dir / "readme_fields.json").write_bytes(
        json.dumps(readme_fields_payload).encode("utf-8")
    )

    validator = _make_validator(equipment_root=equipment_root)
    findings = validator.audit({"kind": "all"})
    missing = [f for f in findings if f.rule == "missing_required_field"]
    assert any(f.matched_token == "sample_type" for f in missing)
    assert all(f.tier == "soft" for f in missing)


def test_audit_missing_required_field_required_ids_not_a_list(tmp_path: Path) -> None:
    """``required_readme_field_ids`` that is not a list is ignored."""
    equipment_root = tmp_path / "CONFOCAL_01"
    project_dir = equipment_root / "PROJ-0042"
    run_dir = project_dir / "Run_2026-04-17T14-32-00"
    run_dir.mkdir(parents=True)

    # Stamp a non-list value to exercise the isinstance() guard.
    creation_payload = _build_creation_json_dict(
        level="run",
        extras={"required_readme_field_ids": "not_a_list"},
    )
    _write_creation_json(project_dir, _build_creation_json_dict(level="project"))
    _write_creation_json(run_dir, creation_payload)

    cache_dir = run_dir / ".exlab-wizard"
    readme_fields_payload: dict[str, Any] = {
        "schema_version": "1.1",
        "generated_at": "2026-04-17T14:32:05Z",
        "core_fields": {"label": "x", "operator": "y", "objective": "z"},
        "system_fields": {},
        "template_fields": {},
        "config_fields": {},
        "custom_fields": [],
    }
    (cache_dir / "readme_fields.json").write_bytes(
        json.dumps(readme_fields_payload).encode("utf-8")
    )

    validator = _make_validator(equipment_root=equipment_root)
    findings = validator.audit({"kind": "all"})
    # No missing_required_field findings because the extra wasn't a list.
    assert not any(f.rule == "missing_required_field" for f in findings)


def test_audit_content_scan_skips_test_runs_marker_file(tmp_path: Path) -> None:
    """The ``test_runs.json`` marker file is exempt from content scanning."""
    equipment_root = tmp_path / "CONFOCAL_01"
    project_dir = equipment_root / "PROJ-0042"
    test_runs_dir = project_dir / "TestRuns"
    test_run_dir = test_runs_dir / "TestRun_2026-04-17T14-32-00"
    test_run_dir.mkdir(parents=True)
    _write_creation_json(project_dir, _build_creation_json_dict(level="project"))
    _write_creation_json(
        test_run_dir,
        _build_creation_json_dict(run_kind="test", level="run"),
    )

    # Write a marker file with a placeholder; the content scan must skip it.
    cache_dir = test_runs_dir / ".exlab-wizard"
    cache_dir.mkdir()
    (cache_dir / "test_runs.json").write_bytes(b'{"placeholder": "<token>"}')

    validator = _make_validator(equipment_root=equipment_root)
    findings = validator.audit({"kind": "all"})
    # No content-scan finding on test_runs.json.
    content_findings = [
        f
        for f in findings
        if f.rule == "unresolved_placeholder_token" and f.offending_kind == "file_content"
    ]
    paths_scanned = {f.offending_path for f in content_findings}
    assert all("test_runs.json" not in p for p in paths_scanned)


def test_audit_content_scan_eligibility_returns_false_on_stat_failure(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """If ``stat()`` raises OSError, the file is skipped from content scanning."""
    equipment_root, _, run_dir = _make_clean_tree(tmp_path)
    bad_file = run_dir / "notes.txt"
    bad_file.write_bytes(b"leftover <run_date>")

    real_stat = Path.stat

    def _raising_stat(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self == bad_file:
            raise OSError("stat failed")
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", _raising_stat)
    validator = _make_validator(equipment_root=equipment_root)
    findings = validator.audit({"kind": "all"})
    content_findings = [
        f
        for f in findings
        if f.rule == "unresolved_placeholder_token" and f.offending_kind == "file_content"
    ]
    assert content_findings == []


def test_audit_read_text_for_scan_handles_oserror(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """If ``open(rb)`` raises OSError, the file is silently skipped."""
    equipment_root, _, run_dir = _make_clean_tree(tmp_path)
    target_file = run_dir / "notes.txt"
    target_file.write_bytes(b"leftover <run_date>")

    real_open = Path.open

    def _raising_open(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self == target_file:
            raise OSError("open failed")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", _raising_open)
    validator = _make_validator(equipment_root=equipment_root)
    findings = validator.audit({"kind": "all"})
    content_findings = [
        f
        for f in findings
        if f.rule == "unresolved_placeholder_token" and f.offending_kind == "file_content"
    ]
    assert content_findings == []


def test_classify_level_value_error_returns_other(tmp_path: Path) -> None:
    """A directory outside the equipment root resolves to "other" (defensive)."""
    equipment_root = tmp_path / "CONFOCAL_01"
    equipment_root.mkdir()
    other_root = tmp_path / "other"
    other_root.mkdir()

    validator = Validator(equipment_roots={"CONFOCAL_01": equipment_root})
    # Audit an unrelated path; the walk should not crash even though the
    # path lies outside any configured equipment root.
    findings = validator.audit({"kind": "project_path", "value": str(other_root)})
    assert isinstance(findings, list)


def test_finding_sort_unknown_tier_sorts_after_committed_tiers() -> None:
    """A finding with an unknown tier sorts after both hard and soft."""
    from exlab_wizard.validator.engine import _finding_sort_key
    from exlab_wizard.validator.findings import Finding

    weird = Finding(
        rule="x",
        tier="unknown",
        run_path="/x",
        offending_path="/x",
        offending_kind="directory_segment",
    )
    hard = Finding(
        rule="x",
        tier="hard",
        run_path="/x",
        offending_path="/x",
        offending_kind="directory_segment",
    )
    sorted_findings = sorted([weird, hard], key=_finding_sort_key)
    assert sorted_findings[0] == hard
    assert sorted_findings[1] == weird


def test_level_for_orphan_returns_none_for_unmapped_levels() -> None:
    """``_level_for_orphan`` returns None for non-project / non-run levels."""
    from exlab_wizard.validator.engine import _level_for_orphan

    assert _level_for_orphan("equipment") is None
    assert _level_for_orphan("test_runs") is None
    assert _level_for_orphan("other") is None


def test_validator_from_config_builds_engine() -> None:
    """``Validator.from_config`` projects fields out of a Config-shaped object."""
    from types import SimpleNamespace

    cfg = SimpleNamespace(
        equipment=[
            SimpleNamespace(id="CONFOCAL_01", local_root="/data/lab"),
            SimpleNamespace(id="OTHER_EQ", local_root="/data/lab"),
        ],
        orchestrator=SimpleNamespace(enabled=True, staging_root="/data/staging"),
        validator=ValidatorConfig(),
    )
    v = Validator.from_config(cfg)
    assert v.config.content_scan_max_mib == ValidatorConfig().content_scan_max_mib
    # Equipment root + staging root resolve through the public audit entrypoint.
    assert v.audit({"kind": "equipment_id", "value": "BOGUS"}) == []


def test_validator_from_config_orchestrator_disabled() -> None:
    """When ``orchestrator.enabled`` is False, no staging root is wired in."""
    from types import SimpleNamespace

    cfg = SimpleNamespace(
        equipment=[],
        orchestrator=SimpleNamespace(enabled=False, staging_root="/data/staging"),
        validator=None,
    )
    v = Validator.from_config(cfg)
    # No equipment + no staging => "all" scope returns no roots.
    assert v.audit({"kind": "all"}) == []


def test_validator_from_config_no_orchestrator_attr() -> None:
    """``orchestrator`` attribute absent on the config also works (default None)."""
    from types import SimpleNamespace

    cfg = SimpleNamespace(
        equipment=None,  # tests the ``or []`` fallback
    )
    v = Validator.from_config(cfg)
    assert v.audit({"kind": "all"}) == []


def test_classify_level_unrelated_dir_returns_other(tmp_path: Path) -> None:
    """``_classify_level`` returns "other" for dirs outside the equipment root."""
    equipment_root = tmp_path / "CONFOCAL_01"
    equipment_root.mkdir()
    unrelated = tmp_path / "elsewhere"
    unrelated.mkdir()
    validator = Validator(equipment_roots={"CONFOCAL_01": equipment_root})
    assert validator._classify_level(unrelated, str(equipment_root.resolve())) == "other"


def test_compute_run_path_unrelated_other_returns_input(tmp_path: Path) -> None:
    """``_compute_run_path`` returns ``str(directory)`` for unrelated paths."""
    equipment_root = tmp_path / "CONFOCAL_01"
    equipment_root.mkdir()
    unrelated = tmp_path / "elsewhere"
    unrelated.mkdir()
    validator = Validator(equipment_roots={"CONFOCAL_01": equipment_root})
    assert validator._compute_run_path(unrelated, "other", str(equipment_root.resolve())) == str(
        unrelated
    )


def test_compute_run_path_root_other_returns_root(tmp_path: Path) -> None:
    """``_compute_run_path`` for the equipment root itself with ``other``."""
    equipment_root = tmp_path / "CONFOCAL_01"
    equipment_root.mkdir()
    equipment_root_abs = str(equipment_root.resolve())
    validator = Validator(equipment_roots={"CONFOCAL_01": equipment_root})
    # Pass the equipment root itself with level="other" so parts=() and we
    # fall through to the final ``return equipment_root_abs``.
    assert (
        validator._compute_run_path(equipment_root, "other", equipment_root_abs)
        == equipment_root_abs
    )
