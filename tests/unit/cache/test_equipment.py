"""Unit tests for ``exlab_wizard.cache.equipment``.

The schemas come from ``exlab_wizard.api.schemas`` (Agent B's scope); these
tests pin the writer's stamping rules (§11.4.1: ``first_seen_at`` preserved,
``last_modified_at`` refreshed) and the test-runs marker idempotency
(§11.4.2: never rewritten after the first write).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import msgspec
import pytest

from exlab_wizard.api.schemas import EquipmentJson
from exlab_wizard.api.schemas import TestRunsJson as TRMarkerJson
from exlab_wizard.cache.equipment import EquipmentCacheWriter
from exlab_wizard.constants import EQUIPMENT_JSON_VERSION, TEST_RUNS_JSON_VERSION

# ``TestRunsJson`` is imported under the alias ``TRMarkerJson`` so that
# pytest does not auto-collect it as a test class (pytest collects any
# top-level identifier in a ``test_*.py`` file whose name starts with
# ``Test`` and which exposes a no-arg constructor). Using the alias keeps
# the spec-mandated class name in ``api/schemas.py`` while letting the
# tests reference it without triggering collection-time errors.


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_equipment_payload(
    equipment_id: str = "CONFOCAL_01",
    label: str = "Confocal Microscope 1",
    local_root: str = "/data/lab",
    nas_root: str = "//nas01/lab",
    *,
    first_seen_at: str = "",
    last_modified_at: str = "",
) -> EquipmentJson:
    """Build a :class:`EquipmentJson` payload for tests.

    Timestamps are stamped by the writer; tests pass empty strings (the
    writer overwrites them) so we don't need to know the canonical format
    in the test body. Schema version comes from the constants module so
    tests pin against the spec rather than a hard-coded literal.
    """
    return EquipmentJson(
        schema_version=EQUIPMENT_JSON_VERSION,
        id=equipment_id,
        label=label,
        configured_local_root=local_root,
        configured_nas_root=nas_root,
        first_seen_at=first_seen_at,
        last_modified_at=last_modified_at,
    )


def _make_test_runs_payload(
    project: str = "PROJ-0042",
    equipment: str = "CONFOCAL_01",
    *,
    created_at: str = "2026-04-17T14:00:00Z",
) -> TRMarkerJson:
    return TRMarkerJson(
        schema_version=TEST_RUNS_JSON_VERSION,
        run_kind="test",
        created_at=created_at,
        project=project,
        equipment=equipment,
    )


# ---------------------------------------------------------------------------
# write_equipment / read_equipment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_equipment_produces_valid_v1_file(tmp_path: Path) -> None:
    """First write produces a parseable v1.0 file with both timestamps stamped."""
    writer = EquipmentCacheWriter()
    path = tmp_path / "CONFOCAL_01" / ".exlab-wizard" / "equipment.json"
    await writer.write_equipment(path, _make_equipment_payload())
    on_disk = msgspec.json.decode(path.read_bytes(), type=EquipmentJson)
    assert on_disk.schema_version == EQUIPMENT_JSON_VERSION
    assert on_disk.id == "CONFOCAL_01"
    assert on_disk.label == "Confocal Microscope 1"
    assert on_disk.configured_local_root == "/data/lab"
    assert on_disk.configured_nas_root == "//nas01/lab"
    # Timestamps are stamped by the writer; both fields must be non-empty
    # and ISO 8601 with the trailing ``Z``.
    assert on_disk.first_seen_at.endswith("Z")
    assert on_disk.last_modified_at.endswith("Z")
    # On a brand-new write the two timestamps are equal (writer uses one
    # ``now`` for both).
    assert on_disk.first_seen_at == on_disk.last_modified_at


@pytest.mark.asyncio
async def test_write_equipment_creates_parent_cache_dir(tmp_path: Path) -> None:
    """Parent ``.exlab-wizard`` is auto-created if missing."""
    writer = EquipmentCacheWriter()
    path = tmp_path / "deep" / "tree" / ".exlab-wizard" / "equipment.json"
    assert not path.parent.exists()
    await writer.write_equipment(path, _make_equipment_payload())
    assert path.is_file()


@pytest.mark.asyncio
async def test_write_equipment_preserves_first_seen_at_on_rewrite(tmp_path: Path) -> None:
    """Re-writing an existing file keeps the original ``first_seen_at``."""
    writer = EquipmentCacheWriter()
    path = tmp_path / "equipment.json"
    await writer.write_equipment(path, _make_equipment_payload(label="v1"))
    original = msgspec.json.decode(path.read_bytes(), type=EquipmentJson)
    original_first_seen = original.first_seen_at
    # Wait at least one second so the second write's timestamp can differ.
    await asyncio.sleep(1.1)
    await writer.write_equipment(path, _make_equipment_payload(label="v2-renamed"))
    rewritten = msgspec.json.decode(path.read_bytes(), type=EquipmentJson)
    assert rewritten.first_seen_at == original_first_seen
    # The label change DID land.
    assert rewritten.label == "v2-renamed"


@pytest.mark.asyncio
async def test_write_equipment_updates_last_modified_at_on_rewrite(tmp_path: Path) -> None:
    """``last_modified_at`` advances on every write; ``first_seen_at`` is frozen."""
    writer = EquipmentCacheWriter()
    path = tmp_path / "equipment.json"
    await writer.write_equipment(path, _make_equipment_payload())
    first = msgspec.json.decode(path.read_bytes(), type=EquipmentJson)
    await asyncio.sleep(1.1)
    await writer.write_equipment(path, _make_equipment_payload())
    second = msgspec.json.decode(path.read_bytes(), type=EquipmentJson)
    assert second.first_seen_at == first.first_seen_at
    assert second.last_modified_at != first.last_modified_at
    assert second.last_modified_at > first.last_modified_at  # ISO 8601 sorts lex.


@pytest.mark.asyncio
async def test_read_equipment_round_trips(tmp_path: Path) -> None:
    """A round-trip write → read returns an equivalent payload."""
    writer = EquipmentCacheWriter()
    path = tmp_path / "equipment.json"
    await writer.write_equipment(path, _make_equipment_payload(label="round-trip"))
    on_disk = await writer.read_equipment(path)
    assert isinstance(on_disk, EquipmentJson)
    assert on_disk.id == "CONFOCAL_01"
    assert on_disk.label == "round-trip"
    assert on_disk.configured_local_root == "/data/lab"
    assert on_disk.configured_nas_root == "//nas01/lab"
    assert on_disk.schema_version == EQUIPMENT_JSON_VERSION
    assert on_disk.first_seen_at.endswith("Z")
    assert on_disk.last_modified_at.endswith("Z")


@pytest.mark.asyncio
async def test_read_equipment_raises_when_file_missing(tmp_path: Path) -> None:
    writer = EquipmentCacheWriter()
    path = tmp_path / "does-not-exist.json"
    with pytest.raises(FileNotFoundError):
        await writer.read_equipment(path)


@pytest.mark.asyncio
async def test_write_equipment_corrupt_existing_file_recovers(tmp_path: Path) -> None:
    """A corrupt existing file is rewritten cleanly; first_seen_at is fresh.

    The §4.4.5 cache contract treats malformed cache files as recoverable
    by being rewritten. The writer must not block the operator's
    creation flow on a broken registry file.
    """
    writer = EquipmentCacheWriter()
    path = tmp_path / "equipment.json"
    path.write_bytes(b"{not valid json at all")
    await writer.write_equipment(path, _make_equipment_payload(label="recovered"))
    on_disk = msgspec.json.decode(path.read_bytes(), type=EquipmentJson)
    assert on_disk.label == "recovered"
    assert on_disk.first_seen_at.endswith("Z")


# ---------------------------------------------------------------------------
# write_test_runs_marker
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_test_runs_marker_creates_v1_file(tmp_path: Path) -> None:
    writer = EquipmentCacheWriter()
    path = tmp_path / "TestRuns" / ".exlab-wizard" / "test_runs.json"
    await writer.write_test_runs_marker(path, _make_test_runs_payload())
    on_disk = msgspec.json.decode(path.read_bytes(), type=TRMarkerJson)
    assert on_disk.schema_version == TEST_RUNS_JSON_VERSION
    assert on_disk.run_kind == "test"
    assert on_disk.project == "PROJ-0042"
    assert on_disk.equipment == "CONFOCAL_01"
    assert on_disk.created_at == "2026-04-17T14:00:00Z"


@pytest.mark.asyncio
async def test_write_test_runs_marker_creates_parent_dir(tmp_path: Path) -> None:
    writer = EquipmentCacheWriter()
    path = tmp_path / "deep" / "TestRuns" / ".exlab-wizard" / "test_runs.json"
    assert not path.parent.exists()
    await writer.write_test_runs_marker(path, _make_test_runs_payload())
    assert path.is_file()


@pytest.mark.asyncio
async def test_write_test_runs_marker_is_idempotent(tmp_path: Path) -> None:
    """Second write is a no-op even when the input payload differs.

    Per §11.4.2: subsequent test-run creations under the same project do
    NOT rewrite this file. The first call captures the marker; later
    calls leave the on-disk content alone.
    """
    writer = EquipmentCacheWriter()
    path = tmp_path / "test_runs.json"
    first_payload = _make_test_runs_payload(project="PROJ-0042", created_at="2026-04-17T14:00:00Z")
    second_payload = _make_test_runs_payload(project="PROJ-9999", created_at="2099-01-01T00:00:00Z")
    await writer.write_test_runs_marker(path, first_payload)
    bytes_after_first = path.read_bytes()
    await writer.write_test_runs_marker(path, second_payload)
    bytes_after_second = path.read_bytes()
    # Byte-identical: second write must not have touched the file.
    assert bytes_after_first == bytes_after_second
    # Content reflects the FIRST write's payload.
    on_disk = msgspec.json.decode(bytes_after_second, type=TRMarkerJson)
    assert on_disk.project == "PROJ-0042"
    assert on_disk.created_at == "2026-04-17T14:00:00Z"


@pytest.mark.asyncio
async def test_write_test_runs_marker_concurrent_first_writes_safe(tmp_path: Path) -> None:
    """Concurrent first-time writes do not corrupt the file.

    Two coroutines race to create the marker; the per-file advisory lock
    in :class:`EquipmentCacheWriter` serializes them so the loser's
    existence-check sees the winner's file and short-circuits.
    """
    writer = EquipmentCacheWriter()
    path = tmp_path / "test_runs.json"
    payloads = [
        _make_test_runs_payload(project=f"PROJ-{i:04d}", created_at="2026-04-17T14:00:00Z")
        for i in range(5)
    ]
    await asyncio.gather(*(writer.write_test_runs_marker(path, p) for p in payloads))
    assert path.is_file()
    on_disk = msgspec.json.decode(path.read_bytes(), type=TRMarkerJson)
    # The winning write's project must be one of the candidates; the loss
    # of the others is silent (idempotent contract).
    assert on_disk.project in {p.project for p in payloads}
