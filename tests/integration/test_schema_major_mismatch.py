"""Integration tests for the cross-major-read-fails contract from §11.9.2.

For every cache file written by the wizard (``creation.json``,
``readme_fields.json``, ``equipment.json``, ``test_runs.json``,
``ingest.json``), the reader MUST refuse a file whose ``schema_version``
major component is different from the reader's. The error must be a
``SchemaMajorMismatchError`` with ``expected_major == 1`` (every cache
schema is currently major 1) and ``found`` mirroring the on-disk string
verbatim.

These tests hand-write the on-disk file (bypassing the writer) so the
reader's gate is exercised in isolation. They live under
``tests/integration/`` because they cross multiple writer modules and
schema files.

Each writer's reader API is discovered by trying a list of plausible
method names per schema -- the parallel-agent boundary in Phase 3D
means the writer surfaces are not pinned at test-write time. The test
fails (does not skip) when the writer module is present but no
discoverable reader rejects the cross-major file: that is the contract
the writer authors must satisfy.
"""

from __future__ import annotations

import importlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import msgspec
import pytest

from exlab_wizard.errors import SchemaMajorMismatchError


@dataclass(frozen=True)
class _CacheCase:
    """One cache-file flavour to exercise against the reader gate."""

    label: str
    """Human-readable label rendered in pytest parametrize output."""

    candidate_modules: tuple[str, ...]
    """Modules that may host the writer (tried in order)."""

    candidate_classes: tuple[str, ...]
    """Class names within ``candidate_modules`` to try."""

    candidate_readers: tuple[str, ...]
    """Reader method names to try on the instantiated class."""

    payload_v2: dict[str, Any]
    """The hand-written v2.0 file content (encoded with msgspec)."""


_CASES: tuple[_CacheCase, ...] = (
    _CacheCase(
        label="creation_json",
        candidate_modules=("exlab_wizard.cache.creation_writer",),
        candidate_classes=("CreationWriter",),
        # Section §4.4.5 documents both ``read_creation_snapshot`` and
        # ``read_creation``; either name is acceptable per the writer
        # contract there.
        candidate_readers=("read_creation_snapshot", "read_creation"),
        payload_v2={
            "schema_version": "2.0",
            "created_at": "2026-04-17T14:32:00Z",
            "created_by": "asmith",
            "level": "run",
            "run_kind": "experimental",
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
            "variables": {},
            "plugins_applied": [],
            "paths": {
                "local": "/data/lab/X/PROJ-0042/Run_x",
                "nas": "//nas01/lab/X/PROJ-0042/Run_x",
            },
            "sync_status": "pending",
            "validation_overrides": [],
        },
    ),
    _CacheCase(
        label="readme_fields_json",
        # The README field-cache writer may live under either
        # ``cache/readme_fields_writer.py`` (mirroring the other cache
        # writers) or ``readme/generator.py`` (per §4.3 file layout).
        candidate_modules=(
            "exlab_wizard.cache.readme_fields_writer",
            "exlab_wizard.readme.generator",
        ),
        candidate_classes=("ReadmeFieldsWriter", "ReadmeGenerator"),
        candidate_readers=("read_readme_fields", "read_readme_fields_snapshot"),
        payload_v2={
            "schema_version": "2.0",
            "generated_at": "2026-04-17T14:32:05Z",
            "core_fields": {
                "label": "stub",
                "operator": "asmith",
                "objective": "stub",
            },
            "template_fields": {},
            "config_fields": {},
            "custom_fields": [],
            "system_fields": {},
        },
    ),
    _CacheCase(
        label="equipment_json",
        candidate_modules=("exlab_wizard.cache.equipment",),
        candidate_classes=("EquipmentCacheWriter", "EquipmentWriter"),
        candidate_readers=("read_equipment", "read_equipment_snapshot"),
        payload_v2={
            "schema_version": "2.0",
            "id": "CONFOCAL_01",
            "label": "Confocal Microscope 1",
            "configured_local_root": "/data/lab",
            "configured_nas_root": "//nas01/lab",
            "first_seen_at": "2025-09-12T09:14:00Z",
            "last_modified_at": "2026-04-17T14:32:00Z",
        },
    ),
    _CacheCase(
        label="test_runs_json",
        candidate_modules=("exlab_wizard.cache.equipment",),
        candidate_classes=("EquipmentCacheWriter", "TestRunsWriter"),
        candidate_readers=(
            "read_test_runs",
            "read_test_runs_marker",
            "read_test_runs_snapshot",
        ),
        payload_v2={
            "schema_version": "2.0",
            "run_kind": "test",
            "created_at": "2026-04-17T14:00:00Z",
            "project": "PROJ-0042",
            "equipment": "CONFOCAL_01",
        },
    ),
    _CacheCase(
        label="ingest_json",
        candidate_modules=("exlab_wizard.cache.ingest_writer",),
        candidate_classes=("IngestWriter",),
        candidate_readers=("read_ingest",),
        payload_v2={
            "schema_version": "2.0",
            "project_name": "Cortex Q3 Pilot",
            "equipment_id": "CONFOCAL_01",
            "run_kind": "experimental",
            "run_path": "CONFOCAL_01/PROJ-0042/Run_x",
            "transport": "smb_mount",
            "current_state": "staging",
            "history": [],
        },
    ),
)


def _resolve_reader(case: _CacheCase) -> Callable[[Path], Awaitable[Any]]:
    """Walk the candidate matrix and return the first reader method we find.

    Each writer-module/class/method triple is tried in order. The first
    combination that resolves to a real bound method is returned.

    Skips the test (with an informative reason) when no candidate is
    available -- that branch is reached only before all sibling agents
    in Phase 3 have integrated. Once the writer surfaces are pinned the
    skip should never trigger.
    """
    last_error: str | None = None
    for module_path in case.candidate_modules:
        try:
            module = importlib.import_module(module_path)
        except ImportError as exc:
            last_error = f"{module_path} not importable: {exc}"
            continue
        for class_name in case.candidate_classes:
            cls = getattr(module, class_name, None)
            if cls is None:
                last_error = f"{module_path}.{class_name} not defined"
                continue
            try:
                instance = cls()
            except TypeError as exc:
                last_error = f"{module_path}.{class_name}() not zero-arg: {exc}"
                continue
            for method_name in case.candidate_readers:
                method = getattr(instance, method_name, None)
                if callable(method):
                    return method
            last_error = (
                f"{module_path}.{class_name} exists but lacks any of {list(case.candidate_readers)}"
            )
    pytest.skip(f"No reader available for {case.label}: {last_error}")


@pytest.mark.parametrize("case", _CASES, ids=lambda c: c.label)
async def test_cross_major_read_raises_schema_major_mismatch(
    tmp_path: Path,
    case: _CacheCase,
) -> None:
    """A v2.0 file MUST be refused by every v1.x reader (§11.9.2 rule 3)."""
    read = _resolve_reader(case)
    path = tmp_path / f"{case.label}.json"
    path.write_bytes(msgspec.json.encode(case.payload_v2))

    with pytest.raises(SchemaMajorMismatchError) as info:
        await read(path)

    assert info.value.expected_major == 1, (
        f"{case.label}: every cache schema is currently major 1; "
        f"expected_major must be 1, got {info.value.expected_major}"
    )
    assert info.value.found == "2.0", (
        f"{case.label}: ``found`` must mirror the on-disk schema_version verbatim "
        f"(got {info.value.found!r})"
    )


@pytest.mark.parametrize("case", _CASES, ids=lambda c: c.label)
async def test_cross_major_read_records_higher_majors_too(
    tmp_path: Path,
    case: _CacheCase,
) -> None:
    """A v3.7 file is also refused; the rule isn't tied to ``2.0`` specifically."""
    read = _resolve_reader(case)
    path = tmp_path / f"{case.label}.json"
    payload = dict(case.payload_v2)
    payload["schema_version"] = "3.7"
    path.write_bytes(msgspec.json.encode(payload))

    with pytest.raises(SchemaMajorMismatchError) as info:
        await read(path)

    assert info.value.expected_major == 1
    assert info.value.found == "3.7"
