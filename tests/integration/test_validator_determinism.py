"""Integration test: ``Validator.audit`` is byte-deterministic across runs.

Backend Spec §11.8 commits the determinism contract::

    Given identical inputs (path layout, file contents, creation.json
    payloads, AND config.yaml validator.* settings), both modes
    produce byte-identical finding lists.

This test stands up a fixture tree containing a representative mix of
findings (orphan, mode-prefix mismatch, unresolved placeholders in
directory names, file names, and file contents, illegal-character
violations) and asserts that two consecutive ``audit`` calls return the
exact same list -- not just equal in length, not just sorted the same,
but item-wise identical including the §11.8 finding fields
(``run_path``, ``offending_path``, ``matched_token``, ``rule``,
``tier``, ``offending_kind``, ``rule_detail``,
``synced_under_prior_policy``, ``override_active``).

The byte-identity check goes one step further: the two finding lists
serialize to the same JSON bytes via ``msgspec.json.encode``. This is
the §11.8 contract literally interpreted -- a downstream pub-sub
channel that diffs snapshots needs the bytes to compare equal, not
just the in-memory dataclasses.

Lives under ``tests/integration/`` (not ``tests/unit/``) because it
exercises the full audit-mode walk against a non-trivial multi-level
tree -- the unit suite covers the per-finding-shape contracts; this
test pins the cross-call invariant on top of the same engine.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import msgspec
from msgspec import json as msgspec_json

from exlab_wizard.config.models import ValidatorConfig
from exlab_wizard.validator.engine import Validator
from exlab_wizard.validator.findings import Finding

# ---------------------------------------------------------------------------
# Fixture builder
# ---------------------------------------------------------------------------


def _build_creation_json_dict(
    *,
    run_kind: str = "experimental",
    sync_status: str = "pending",
    validation_overrides: list[dict[str, Any]] | None = None,
    level: str = "run",
) -> dict[str, Any]:
    return {
        "schema_version": "1.9",
        "created_at": "2026-04-17T14:32:00Z",
        "created_by": "asmith",
        "level": level,
        "run_kind": run_kind,
        "lims_project": {
            "uid": "8c7e9d2f-1a4b-4e6c-9b3d-7f2a1e5d8c4b",
            "short_id": "PROJ-0042",
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


def _write_creation_json(directory: Path, payload: dict[str, Any]) -> None:
    cache_dir = directory / ".exlab-wizard"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "creation.json").write_bytes(json.dumps(payload).encode("utf-8"))


def _build_fixture_tree(tmp_path: Path) -> Path:
    """Construct a tree with several findings in stable on-disk shape.

    The tree contains, in addition to a clean run, the following
    findings the audit walk must surface:

    * orphan -- a project directory with no ``creation.json``.
    * mode-prefix mismatch -- a ``Run_*`` leaf whose ``creation.json``
      says ``run_kind="test"``.
    * unresolved-placeholder (directory) -- a ``Run_<placeholder>``
      leaf.
    * unresolved-placeholder (file content) -- a small ``.txt`` file
      with an angle-bracket token.
    * synced-under-prior-policy -- a hard finding on a synced run.

    Returns the equipment root used as the audit scope.
    """
    equipment_root = tmp_path / "CONFOCAL_01"

    # Clean project + run.
    clean_project = equipment_root / "PROJ-0001"
    clean_run = clean_project / "Run_2026-04-17T14-32-00"
    clean_run.mkdir(parents=True)
    _write_creation_json(clean_project, _build_creation_json_dict(level="project"))
    _write_creation_json(clean_run, _build_creation_json_dict(level="run"))

    # Orphan project (no creation.json at the project level).
    orphan_project = equipment_root / "PROJ-0002"
    orphan_run = orphan_project / "Run_2026-04-17T14-32-00"
    orphan_run.mkdir(parents=True)
    _write_creation_json(orphan_run, _build_creation_json_dict(level="run"))

    # Mode-prefix mismatch run (Run_* leaf with run_kind=test).
    mismatch_project = equipment_root / "PROJ-0003"
    mismatch_run = mismatch_project / "Run_2026-04-17T14-32-00"
    mismatch_run.mkdir(parents=True)
    _write_creation_json(mismatch_project, _build_creation_json_dict(level="project"))
    _write_creation_json(
        mismatch_run,
        _build_creation_json_dict(run_kind="test", level="run"),
    )

    # Placeholder in directory name.
    placeholder_project = equipment_root / "PROJ-0004"
    placeholder_run = placeholder_project / "Run_<run_date>"
    placeholder_run.mkdir(parents=True)
    _write_creation_json(placeholder_project, _build_creation_json_dict(level="project"))
    _write_creation_json(placeholder_run, _build_creation_json_dict(level="run"))

    # Placeholder in file content.
    content_project = equipment_root / "PROJ-0005"
    content_run = content_project / "Run_2026-04-17T14-32-00"
    content_run.mkdir(parents=True)
    _write_creation_json(content_project, _build_creation_json_dict(level="project"))
    _write_creation_json(content_run, _build_creation_json_dict(level="run"))
    (content_run / "notes.txt").write_bytes(b"leftover <run_date> in body\n")

    # Synced-under-prior-policy: a placeholder finding on a synced run.
    synced_project = equipment_root / "PROJ-0006"
    synced_run = synced_project / "Run_<placeholder>"
    synced_run.mkdir(parents=True)
    _write_creation_json(synced_project, _build_creation_json_dict(level="project"))
    _write_creation_json(
        synced_run,
        _build_creation_json_dict(level="run", sync_status="synced"),
    )

    return equipment_root


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


def test_audit_two_calls_return_byte_identical_finding_lists(tmp_path: Path) -> None:
    """Spec §11.8 determinism: two ``audit`` calls produce identical findings.

    The contract: identical inputs -> identical bytes. Two consecutive
    calls against the same fixture tree must produce:

    1. The same number of findings.
    2. The same in-memory dataclass list (order preserved).
    3. The same encoded-JSON bytes (which is the literal §11.8 wire
       format the pub-sub channel diffs).
    """
    equipment_root = _build_fixture_tree(tmp_path)

    config = ValidatorConfig(
        content_scan_max_mib=5,
        content_scan_extensions=[
            ".txt",
            ".md",
            ".csv",
            ".tsv",
            ".json",
            ".yaml",
            ".yml",
            ".toml",
        ],
    )
    validator = Validator(
        validator_config=config,
        equipment_roots={"CONFOCAL_01": equipment_root},
    )

    first = validator.audit({"kind": "all"})
    second = validator.audit({"kind": "all"})

    # Sanity: the fixture must produce findings or the test is vacuous.
    assert first, "expected at least one finding from the determinism fixture"

    # In-memory list equality (the dataclass is frozen + eq=True).
    assert first == second, "audit() returned divergent lists across calls"

    # Per-finding equality (defensive: makes failures more readable).
    assert len(first) == len(second)
    for a, b in zip(first, second, strict=True):
        assert isinstance(a, Finding)
        assert isinstance(b, Finding)
        assert a == b

    # Byte-identical via ``msgspec.json.encode`` -- the §11.8
    # determinism contract is literally about bytes on the pub-sub
    # channel, not just dataclass equality.
    first_payload = [f.to_dict() for f in first]
    second_payload = [f.to_dict() for f in second]
    first_bytes = msgspec_json.encode(first_payload)
    second_bytes = msgspec_json.encode(second_payload)
    assert first_bytes == second_bytes


def test_audit_byte_identical_with_query_problems_alias(tmp_path: Path) -> None:
    """``audit`` and ``query_problems`` produce byte-identical outputs.

    This is the §11.8 alias contract: the GUI calls
    ``query_problems(scope)`` but the engine routes it to ``audit``.
    The two surfaces must produce the same byte stream so a future
    refactor cannot silently divergence them.
    """
    equipment_root = _build_fixture_tree(tmp_path)
    config = ValidatorConfig()
    validator = Validator(
        validator_config=config,
        equipment_roots={"CONFOCAL_01": equipment_root},
    )

    via_audit = validator.audit({"kind": "all"})
    via_query = validator.query_problems({"kind": "all"})

    assert via_audit == via_query
    via_audit_bytes = msgspec.json.encode([f.to_dict() for f in via_audit])
    via_query_bytes = msgspec.json.encode([f.to_dict() for f in via_query])
    assert via_audit_bytes == via_query_bytes
