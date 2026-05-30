"""Tests for the declarative ``SAMPLES`` list and its Pydantic models (§4).

These guard the spec data's shape (2 equipment / 3 projects / 7 runs spanning
the sync-status badge states) and the fail-fast field validators that reject
malformed ids / names at construction time.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from exlab_wizard.constants.enums import RunKind, SyncMode, SyncStatus
from exlab_wizard.sample_data.spec import (
    SAMPLES,
    SampleEquipment,
    SampleFile,
    SampleProject,
    SampleRun,
)


def _project(equipment: SampleEquipment, short_id: str) -> SampleProject:
    return next(p for p in equipment.projects if p.short_id == short_id)


def test_samples_top_level_shape() -> None:
    """Two equipment entries, keyed TESTRIG then ALTRIG."""
    assert len(SAMPLES) == 2
    assert [e.id for e in SAMPLES] == ["TESTRIG", "ALTRIG"]
    assert all(e.sync_mode is SyncMode.NAS for e in SAMPLES)


def test_testrig_has_one_project_three_runs() -> None:
    testrig = SAMPLES[0]
    assert testrig.id == "TESTRIG"
    assert [p.short_id for p in testrig.projects] == ["PROJ-0001"]
    assert len(testrig.projects[0].runs) == 3


def test_altrig_has_two_projects_two_runs_each() -> None:
    altrig = SAMPLES[1]
    assert altrig.id == "ALTRIG"
    assert [p.short_id for p in altrig.projects] == ["PROJ-0002", "PROJ-0003"]
    assert len(_project(altrig, "PROJ-0002").runs) == 2
    assert len(_project(altrig, "PROJ-0003").runs) == 2


def test_total_run_count_is_seven() -> None:
    total = sum(len(p.runs) for e in SAMPLES for p in e.projects)
    assert total == 7


def test_malformed_short_id_raises_validation_error() -> None:
    with pytest.raises(ValidationError):
        SampleProject(
            short_id="proj-0001",  # lower-case -> fails PROJECT_SHORT_ID_PATTERN
            name="Demo Project",
            label="Demo Project",
            operator="asmith",
            objective="x",
            runs=[
                SampleRun(
                    kind=RunKind.EXPERIMENTAL,
                    label="r",
                    operator="asmith",
                    objective="x",
                )
            ],
        )


def test_malformed_equipment_id_raises_validation_error() -> None:
    with pytest.raises(ValidationError):
        SampleEquipment(
            id="test_rig",  # lower-case / underscore-led -> fails equipment grammar
            label="Test Rig",
            projects=[
                SampleProject(
                    short_id="PROJ-0001",
                    name="Demo Project",
                    label="Demo Project",
                    operator="asmith",
                    objective="x",
                    runs=[
                        SampleRun(
                            kind=RunKind.EXPERIMENTAL,
                            label="r",
                            operator="asmith",
                            objective="x",
                        )
                    ],
                )
            ],
        )


def test_malformed_project_name_raises_validation_error() -> None:
    # A path separator survives str-stripping and is rejected by
    # validate_project_name (illegal filesystem character).
    with pytest.raises(ValidationError):
        SampleProject(
            short_id="PROJ-0001",
            name="bad/name",
            label="bad/name",
            operator="asmith",
            objective="x",
            runs=[
                SampleRun(
                    kind=RunKind.EXPERIMENTAL,
                    label="r",
                    operator="asmith",
                    objective="x",
                )
            ],
        )


def test_unknown_key_rejected_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        SampleRun(
            kind=RunKind.EXPERIMENTAL,
            label="r",
            operator="asmith",
            objective="x",
            bogus="nope",  # type: ignore[call-arg]
        )


@pytest.mark.parametrize("bad", ["/abs/data.txt", "../escape.txt", "data/../../escape.txt"])
def test_samplefile_rejects_escaping_relpath(bad: str) -> None:
    """``relpath`` must stay under the run dir -- no absolute or ``..`` paths."""
    with pytest.raises(ValidationError):
        SampleFile(relpath=bad)


def test_samplefile_accepts_nested_relative_path() -> None:
    assert SampleFile(relpath="data/sub/acq_001.csv").relpath == "data/sub/acq_001.csv"


def test_sync_status_spread_covers_required_states() -> None:
    statuses = {r.sync_status for e in SAMPLES for p in e.projects for r in p.runs}
    required = {
        SyncStatus.SYNCED,
        SyncStatus.PENDING,
        SyncStatus.BLOCKED_BY_VALIDATION,
    }
    assert required <= statuses


def test_proj_0003_blocked_run_carries_trigger_file() -> None:
    altrig = SAMPLES[1]
    proj = _project(altrig, "PROJ-0003")
    blocked = next(r for r in proj.runs if r.sync_status is SyncStatus.BLOCKED_BY_VALIDATION)
    assert blocked.files is not None
    assert [f.relpath for f in blocked.files] == ["data/leak.txt"]
    trigger = blocked.files[0]
    assert isinstance(trigger, SampleFile)
    assert "DEMO_SCAN_TRIGGER" in trigger.content


def test_proj_0002_calibration_a_has_readme_extra_fields() -> None:
    altrig = SAMPLES[1]
    proj = _project(altrig, "PROJ-0002")
    cal_a = next(r for r in proj.runs if r.label == "Calibration A")
    assert cal_a.readme_extra.get("sample_type") == "control"
    assert cal_a.readme_extra.get("reviewer") == "asmith"
