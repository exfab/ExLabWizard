"""Declarative sample-data model + the ``SAMPLES`` list (design spec §4).

``SAMPLES`` is the single place a developer edits to change the seeded demo
dataset. The generator (a later phase) expands this list into a real on-disk
tree under the ``-test`` sandbox, writing each folder's metadata through the
same producers the production creation pipeline uses, so the data is correct by
construction and cannot drift from the real format.

Models follow the ``config/models.py`` house style: ``extra="forbid"`` (unknown
keys raise), ``str_strip_whitespace=True`` (incidental whitespace is trimmed),
and ``frozen=True`` (``SAMPLES`` is a static constant). The field validators
reuse the real id/name rules from :mod:`exlab_wizard.paths`, so malformed sample
data raises ``ValidationError`` at import time -- before any folder is touched.
Each validator catches the helper's ``ConfigError`` and re-raises it as
``ValueError`` so Pydantic surfaces it as a ``ValidationError``, mirroring
``EquipmentConfig._validate_equipment_id`` in ``config/models.py``.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from exlab_wizard.constants.enums import RunKind, SyncMode, SyncStatus
from exlab_wizard.errors import ConfigError


class SampleFile(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)

    relpath: str = Field(min_length=1)  # path under the run dir, e.g. "data/acq_001.csv"
    content: str = ""  # deterministic UTF-8 content (kept small)

    @field_validator("relpath")
    @classmethod
    def _check_relpath(cls, v: str) -> str:
        # The generator writes payload files at ``run_dir / relpath``; keep
        # them contained -- reject absolute paths and ``..`` traversal so a
        # sample can never escape its run directory.
        from pathlib import PurePosixPath

        pure = PurePosixPath(v)
        if pure.is_absolute() or ".." in pure.parts:
            msg = f"relpath {v!r} must be a relative path under the run dir, without '..' segments"
            raise ValueError(msg)
        return v


class SampleRun(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)

    kind: RunKind
    label: str = Field(min_length=1)
    operator: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    readme_extra: dict[str, Any] = Field(default_factory=dict)  # config-default + custom values
    sync_status: SyncStatus = SyncStatus.PENDING  # chosen badge scenario for this run
    files: list[SampleFile] | None = None  # payload files; None -> a default pair
    minutes_offset: int | None = None  # deterministic run-date offset; auto if None


class SampleProject(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)

    short_id: str  # LIMS short id (validated below)
    name: str = Field(min_length=1)  # human-readable <project>/ folder segment
    label: str = Field(min_length=1)
    operator: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    runs: list[SampleRun] = Field(min_length=1)

    @field_validator("short_id")
    @classmethod
    def _check_short_id(cls, v: str) -> str:
        from exlab_wizard.paths import validate_project_short_id

        try:
            return validate_project_short_id(v)
        except ConfigError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str) -> str:
        # Fail-fast: a bad folder name raises ValidationError at import time
        # rather than mid-generation. Same rule compose_project_path /
        # compose_run_path enforce when the tree is written.
        from exlab_wizard.paths import validate_project_name

        try:
            return validate_project_name(v)
        except ConfigError as exc:
            raise ValueError(str(exc)) from exc


class SampleEquipment(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)

    id: str  # raw id; TEST_ prefix applied by the loader
    label: str = Field(min_length=1)
    sync_mode: SyncMode = SyncMode.NAS
    projects: list[SampleProject] = Field(min_length=1)

    @field_validator("id")
    @classmethod
    def _check_id(cls, v: str) -> str:
        from exlab_wizard.paths import canonicalize_equipment_id

        try:
            return canonicalize_equipment_id(v)
        except ConfigError as exc:
            raise ValueError(str(exc)) from exc


SAMPLES: list[SampleEquipment] = [
    # Equipment A -- one project, three runs, covers SYNCED + PENDING.
    SampleEquipment(
        id="TESTRIG",
        label="Test Rig",
        sync_mode=SyncMode.NAS,
        projects=[
            SampleProject(
                short_id="PROJ-0001",
                name="Demo Project",
                label="Demo Project",
                operator="asmith",
                objective="Exercise the browse / validate / sync UIs.",
                runs=[
                    SampleRun(
                        kind=RunKind.EXPERIMENTAL,
                        label="Baseline run",
                        operator="asmith",
                        objective="Baseline acquisition.",
                        sync_status=SyncStatus.SYNCED,
                    ),
                    SampleRun(
                        kind=RunKind.EXPERIMENTAL,
                        label="Repeat run",
                        operator="asmith",
                        objective="Repeat for variance.",
                    ),
                    SampleRun(
                        kind=RunKind.TEST,
                        label="Smoke test run",
                        operator="asmith",
                        objective="Test-mode dry run.",
                    ),
                ],
            ),
        ],
    ),
    # Equipment B -- two projects. PROJ-0002 exercises config-default + custom
    # README fields; PROJ-0003 carries the BLOCKED_BY_VALIDATION scenario.
    SampleEquipment(
        id="ALTRIG",
        label="Alt Rig",
        sync_mode=SyncMode.NAS,
        projects=[
            SampleProject(
                short_id="PROJ-0002",
                name="Calibration Study",
                label="Calibration Study",
                operator="bjones",
                objective="Calibration sweep.",
                runs=[
                    SampleRun(
                        kind=RunKind.EXPERIMENTAL,
                        label="Calibration A",
                        operator="bjones",
                        objective="Primary calibration.",
                        sync_status=SyncStatus.SYNCED,
                        # "sample_type" matches the seeded config default -> config_fields;
                        # "reviewer" matches nothing -> custom_fields.
                        readme_extra={"sample_type": "control", "reviewer": "asmith"},
                    ),
                    SampleRun(
                        kind=RunKind.TEST,
                        label="Calibration dry run",
                        operator="bjones",
                        objective="Dry-run the calibration.",
                    ),
                ],
            ),
            SampleProject(
                short_id="PROJ-0003",
                name="Failure Modes",
                label="Failure Modes",
                operator="bjones",
                objective="Reproduce failure modes.",
                runs=[
                    SampleRun(
                        kind=RunKind.EXPERIMENTAL,
                        label="Bad acquisition",
                        operator="bjones",
                        objective="Trips the content scanner.",
                        sync_status=SyncStatus.BLOCKED_BY_VALIDATION,
                        files=[
                            SampleFile(
                                relpath="data/leak.txt",
                                content="api_key=DEMO_SCAN_TRIGGER\n",
                            )
                        ],
                    ),
                    SampleRun(
                        kind=RunKind.TEST,
                        label="Edge case test",
                        operator="bjones",
                        objective="Edge-case dry run.",
                    ),
                ],
            ),
        ],
    ),
]  # the one place a developer edits
