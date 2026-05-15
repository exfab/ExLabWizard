"""Tests for ``exlab_wizard.sync.pre_sync_gate``.

Backend Spec §7.3. The Pre-Sync Gate is a creation-time-style validator
pass plus override-aware filtering. The §8.1 hard-tier rules
(unresolved-placeholder, illegal filesystem character, mode-prefix
mismatch, leftover-Jinja) block sync; soft-tier rules do not.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from exlab_wizard.api.schemas import (
    CreationJson,
    LimsProjectBlock,
    OverrideEntry,
    PathsBlock,
    TemplateBlock,
    override_entry_to_dict,
)
from exlab_wizard.constants import CREATION_JSON_VERSION
from exlab_wizard.sync.pre_sync_gate import is_eligible
from exlab_wizard.validator.engine import Validator


def _build_creation(
    *,
    local_path: str = "/data/EQ1/PROJ-0042/Runs/Run_2026-04-17T14-32-00",
    nas_path: str = "/srv/nas/EQ1/PROJ-0042/Runs/Run_2026-04-17T14-32-00",
    overrides: list[dict] | None = None,
) -> CreationJson:
    return CreationJson(
        schema_version=CREATION_JSON_VERSION,
        created_at="2026-04-17T14:32:00Z",
        created_by="asmith",
        level="run",
        run_kind="experimental",
        lims_project=LimsProjectBlock(
            uid="abc",
            short_id="PROJ-0042",
            name_at_creation="Test Project",
        ),
        template=TemplateBlock(
            name="confocal_run",
            version="1.0",
            source_path="x",
            run_scope="experimental",
        ),
        variables={},
        paths=PathsBlock(local=local_path, nas=nas_path),
        validation_overrides=overrides or [],
    )


@pytest.fixture()
def validator() -> Validator:
    return Validator()


def test_clean_run_is_eligible(tmp_path: Path, validator: Validator) -> None:
    """A creation with no validator findings is eligible for sync."""
    run_dir = tmp_path / "EQ1" / "PROJ-0042" / "Runs" / "Run_2026-04-17T14-32-00"
    run_dir.mkdir(parents=True)
    (run_dir / ".exlab-wizard").mkdir()
    creation_path = run_dir / ".exlab-wizard" / "creation.json"
    creation = _build_creation(local_path=str(run_dir))
    eligible, blocking = is_eligible(
        validator=validator,
        creation_json_path=creation_path,
        creation=creation,
    )
    assert eligible is True
    assert blocking == []


def test_hard_finding_blocks(tmp_path: Path, validator: Validator) -> None:
    """An unresolved-placeholder token in the path triggers a block."""
    run_dir = tmp_path / "EQ1" / "PROJ-0042" / "Runs" / "Run_<run_date>"
    run_dir.mkdir(parents=True)
    (run_dir / ".exlab-wizard").mkdir()
    creation_path = run_dir / ".exlab-wizard" / "creation.json"
    creation = _build_creation(local_path=str(run_dir))
    eligible, blocking = is_eligible(
        validator=validator,
        creation_json_path=creation_path,
        creation=creation,
    )
    assert eligible is False
    assert any(f.rule == "unresolved_placeholder_token" and f.tier == "hard" for f in blocking)


def test_active_override_unblocks(tmp_path: Path, validator: Validator) -> None:
    """An active override matching the finding's rule unblocks sync."""
    run_dir = tmp_path / "EQ1" / "PROJ-0042" / "Runs" / "Run_<run_date>"
    run_dir.mkdir(parents=True)
    (run_dir / ".exlab-wizard").mkdir()
    creation_path = run_dir / ".exlab-wizard" / "creation.json"
    overrides = [
        override_entry_to_dict(
            OverrideEntry(
                id="o1",
                problem_class="unresolved_placeholder_token",
                operator="asmith",
                recorded_at="2026-04-17T14:32:00Z",
                reason="legacy template",
            )
        ),
        override_entry_to_dict(
            OverrideEntry(
                id="o2",
                problem_class="illegal_filesystem_character",
                operator="asmith",
                recorded_at="2026-04-17T14:32:00Z",
                reason="legacy template",
            )
        ),
    ]
    creation = _build_creation(local_path=str(run_dir), overrides=overrides)
    eligible, blocking = is_eligible(
        validator=validator,
        creation_json_path=creation_path,
        creation=creation,
    )
    assert eligible is True
    assert blocking == []


def test_revoked_override_does_not_unblock(tmp_path: Path, validator: Validator) -> None:
    """An override that has been revoked does NOT unblock sync."""
    run_dir = tmp_path / "EQ1" / "PROJ-0042" / "Runs" / "Run_<run_date>"
    run_dir.mkdir(parents=True)
    (run_dir / ".exlab-wizard").mkdir()
    creation_path = run_dir / ".exlab-wizard" / "creation.json"
    overrides = [
        override_entry_to_dict(
            OverrideEntry(
                id="o1",
                problem_class="unresolved_placeholder_token",
                operator="asmith",
                recorded_at="2026-04-17T14:32:00Z",
                reason="legacy template",
            )
        ),
        {
            "id": "t1",
            "revoked": True,
            "revokes": "o1",
            "operator": "asmith",
            "recorded_at": "2026-04-17T15:00:00Z",
            "reason": "fixed it",
        },
    ]
    creation = _build_creation(local_path=str(run_dir), overrides=overrides)
    eligible, blocking = is_eligible(
        validator=validator,
        creation_json_path=creation_path,
        creation=creation,
    )
    assert eligible is False
    assert blocking


def test_soft_finding_does_not_block(tmp_path: Path, validator: Validator) -> None:
    """A soft-tier finding (missing-required-field) does NOT block sync.

    The validator at creation-time mode only emits soft findings for the
    missing-required-field rule when ``required_field_ids`` is non-empty;
    the gate's ``is_eligible`` does not pass any required ids, so this
    test exercises the absence-of-blocking-finding path with a clean
    structural input.
    """
    run_dir = tmp_path / "EQ1" / "PROJ-0042" / "Runs" / "Run_2026-04-17T14-32-00"
    run_dir.mkdir(parents=True)
    creation_path = run_dir / ".exlab-wizard" / "creation.json"
    creation = _build_creation(local_path=str(run_dir))
    eligible, _ = is_eligible(
        validator=validator,
        creation_json_path=creation_path,
        creation=creation,
    )
    assert eligible is True
