"""Tests for the atomic ``creation.json`` writer in
``exlab_wizard.cache.creation_writer``.

The writer is the single mutation surface for ``creation.json`` and is the
contract Backend Spec §4.4.5 bolts down. These tests pin:

* the LOCK_EX-for-full-cycle write semantics (initial write + atomic update),
* the LOCK_SH-for-read semantics (concurrent readers),
* the typed encode/decode round-trip,
* the §11.9.3 forward-compat policy (unknown fields are preserved on
  round-trip),
* the §11.3 history-table migration policy (older minors are silently
  upgraded with the documented defaults; major-version mismatches raise),
* the §11.3 active-overrides matching algorithm extracted as a pure helper.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import msgspec
import pytest

from exlab_wizard.api.schemas import (
    CreationJson,
    LimsProjectBlock,
    OverrideEntry,
    PathsBlock,
    PluginApplied,
    PluginIsolation,
    TemplateBlock,
    msgspec_json,
    override_entry_to_dict,
    tombstone_entry_to_dict,
)
from exlab_wizard.api.schemas import TombstoneEntry as TombstoneEntryStruct
from exlab_wizard.cache.creation_writer import CreationWriter, select_active_overrides
from exlab_wizard.constants import CREATION_JSON_VERSION
from exlab_wizard.errors import SchemaMajorMismatchError

# ---------------------------------------------------------------------------
# Fixtures / builders
# ---------------------------------------------------------------------------


def _build_minimal_payload(**overrides: object) -> CreationJson:
    base = dict(
        schema_version=CREATION_JSON_VERSION,
        created_at="2026-04-17T14:32:00Z",
        created_by="asmith",
        level="run",
        run_kind="experimental",
        lims_project=LimsProjectBlock(
            uid="8c7e9d2f-1a4b-4e6c-9b3d-7f2a1e5d8c4b",
            short_id="PROJ-0042",
            name_at_creation="Cortex Q3 Pilot",
        ),
        template=TemplateBlock(
            name="confocal_run_v2",
            version="2.1",
            source_path="templates/confocal_run_v2",
            run_scope="both",
        ),
        variables={"project_name": "Cortex Q3 Pilot"},
        paths=PathsBlock(local="/x", nas="//y"),
    )
    base.update(overrides)
    return CreationJson(**base)


@pytest.fixture()
def writer() -> CreationWriter:
    return CreationWriter(lock_timeout_seconds=10.0)


@pytest.fixture()
def creation_path(tmp_path: Path) -> Path:
    return tmp_path / "creation.json"


# ---------------------------------------------------------------------------
# write_creation -- happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_creation_emits_valid_v18_file(
    writer: CreationWriter, creation_path: Path
) -> None:
    payload = _build_minimal_payload()
    await writer.write_creation(creation_path, payload)
    assert creation_path.exists()
    decoded = msgspec_json.decode(creation_path.read_bytes(), type=CreationJson)
    assert decoded.schema_version == "1.8"
    assert decoded.lims_project.short_id == "PROJ-0042"


@pytest.mark.asyncio
async def test_write_creation_pins_schema_version_to_writer_default(
    writer: CreationWriter, creation_path: Path
) -> None:
    """Even if the caller hands us an old version, the writer pins to current."""
    payload = _build_minimal_payload(schema_version="1.7")
    await writer.write_creation(creation_path, payload)
    on_disk = msgspec_json.decode(creation_path.read_bytes(), type=dict[str, object])
    assert on_disk["schema_version"] == CREATION_JSON_VERSION


# ---------------------------------------------------------------------------
# update_creation_atomic -- happy path + forward-compat
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_creation_atomic_mutates_target_field(
    writer: CreationWriter, creation_path: Path
) -> None:
    await writer.write_creation(creation_path, _build_minimal_payload())

    def mutator(p: CreationJson) -> CreationJson:
        p.sync_status = "synced"
        return p

    new_payload = await writer.update_creation_atomic(creation_path, mutator)
    assert new_payload.sync_status == "synced"
    on_disk = msgspec_json.decode(creation_path.read_bytes(), type=CreationJson)
    assert on_disk.sync_status == "synced"


@pytest.mark.asyncio
async def test_update_creation_atomic_preserves_unknown_top_level_fields(
    writer: CreationWriter, creation_path: Path
) -> None:
    """Forward-compat: a v0.7 writer must not drop fields a v0.8 writer added."""
    raw = msgspec_json.decode(msgspec_json.encode(_build_minimal_payload()), type=dict[str, object])
    raw["future_field_for_v_2_0"] = {"hello": "world"}
    creation_path.write_bytes(msgspec_json.encode(raw))

    def mutator(p: CreationJson) -> CreationJson:
        p.sync_status = "synced"
        return p

    await writer.update_creation_atomic(creation_path, mutator)
    after = msgspec_json.decode(creation_path.read_bytes(), type=dict[str, object])
    assert after["future_field_for_v_2_0"] == {"hello": "world"}
    assert after["sync_status"] == "synced"


@pytest.mark.asyncio
async def test_update_creation_atomic_appends_validation_override(
    writer: CreationWriter, creation_path: Path
) -> None:
    await writer.write_creation(creation_path, _build_minimal_payload())

    def mutator(p: CreationJson) -> CreationJson:
        p.validation_overrides.append(
            override_entry_to_dict(
                OverrideEntry(
                    id="aa",
                    problem_class="leftover_jinja_marker",
                    operator="asmith",
                    recorded_at="2026-04-18T09:14:22Z",
                    reason="r",
                )
            )
        )
        return p

    after = await writer.update_creation_atomic(creation_path, mutator)
    assert len(after.validation_overrides) == 1
    on_disk = msgspec_json.decode(creation_path.read_bytes(), type=CreationJson)
    assert on_disk.validation_overrides[0]["id"] == "aa"


@pytest.mark.asyncio
async def test_update_creation_atomic_serializes_concurrent_calls(
    writer: CreationWriter, creation_path: Path
) -> None:
    """Two simultaneous mutators on the same path must serialize.

    This is a small-N variant of the integration concurrent-write test;
    it lives here to keep the unit suite covering the lock-for-full-cycle
    invariant directly.
    """
    await writer.write_creation(creation_path, _build_minimal_payload())

    def make_mutator(token: str):
        def mutator(p: CreationJson) -> CreationJson:
            p.plugins_applied.append(
                PluginApplied(
                    plugin=token,
                    version="1.0",
                    files_affected=[],
                    status="success",
                )
            )
            return p

        return mutator

    await asyncio.gather(
        writer.update_creation_atomic(creation_path, make_mutator("a")),
        writer.update_creation_atomic(creation_path, make_mutator("b")),
        writer.update_creation_atomic(creation_path, make_mutator("c")),
    )
    on_disk = msgspec_json.decode(creation_path.read_bytes(), type=CreationJson)
    plugin_names = {entry.plugin for entry in on_disk.plugins_applied}
    assert plugin_names == {"a", "b", "c"}


# ---------------------------------------------------------------------------
# read_creation_snapshot -- shared lock; concurrent reads
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_creation_snapshot_returns_typed_struct(
    writer: CreationWriter, creation_path: Path
) -> None:
    await writer.write_creation(creation_path, _build_minimal_payload())
    snap = await writer.read_creation_snapshot(creation_path)
    assert isinstance(snap, CreationJson)
    assert snap.lims_project.short_id == "PROJ-0042"


@pytest.mark.asyncio
async def test_read_creation_snapshot_supports_concurrent_readers(
    writer: CreationWriter, creation_path: Path
) -> None:
    """Multiple LOCK_SH readers must not block each other."""
    await writer.write_creation(creation_path, _build_minimal_payload())
    results = await asyncio.gather(
        *[writer.read_creation_snapshot(creation_path) for _ in range(8)]
    )
    assert all(isinstance(r, CreationJson) for r in results)
    assert all(r.lims_project.short_id == "PROJ-0042" for r in results)


# ---------------------------------------------------------------------------
# Schema-version migration (§11.3 history; §11.9.2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reading_2_0_file_raises_schema_major_mismatch(
    writer: CreationWriter, creation_path: Path
) -> None:
    raw = msgspec_json.decode(msgspec_json.encode(_build_minimal_payload()), type=dict[str, object])
    raw["schema_version"] = "2.0"
    creation_path.write_bytes(msgspec_json.encode(raw))
    with pytest.raises(SchemaMajorMismatchError) as info:
        await writer.read_creation_snapshot(creation_path)
    assert info.value.found == "2.0"
    assert info.value.expected_major == 1


@pytest.mark.asyncio
async def test_reading_malformed_schema_version_raises_schema_major_mismatch(
    writer: CreationWriter, creation_path: Path
) -> None:
    """A non-MAJOR.MINOR string must surface as the same structured error
    rather than a raw ValueError -- the spec's reader policy gates here."""
    raw = msgspec_json.decode(msgspec_json.encode(_build_minimal_payload()), type=dict[str, object])
    raw["schema_version"] = "garbage"
    creation_path.write_bytes(msgspec_json.encode(raw))
    with pytest.raises(SchemaMajorMismatchError):
        await writer.read_creation_snapshot(creation_path)


@pytest.mark.asyncio
async def test_reading_1_0_file_applies_documented_defaults(
    writer: CreationWriter, creation_path: Path
) -> None:
    """Spec §11.3 history: a 1.0 file predates ``run_kind``,
    ``validation_overrides``, ``lims_project``, etc. The reader fills the
    defaults so it can be parsed against the 1.8 Struct."""
    on_wire = {
        "schema_version": "1.0",
        "created_at": "2024-01-01T00:00:00Z",
        "created_by": "old-user",
        "level": "run",
        # run_kind absent -> defaults to "experimental"
        # validation_overrides absent -> defaults to []
        # lims_project absent in 1.0; we add it because v1.5+ requires it
        # to round-trip as the typed Struct's required field. The reader
        # tolerates absent lims_project on truly old files in production
        # (file_version <= 1.4 are exempt); here we only exercise the
        # documented run_kind / validation_overrides defaults.
        "lims_project": {
            "uid": "x",
            "short_id": "PROJ-OLD",
            "name_at_creation": "old proj",
        },
        "template": {
            "name": "old_tpl",
            "version": "1.0",
            "source_path": "x",
            "run_scope": "both",
        },
        "variables": {},
        "paths": {"local": "/x", "nas": "//y"},
    }
    creation_path.write_bytes(msgspec_json.encode(on_wire))
    snap = await writer.read_creation_snapshot(creation_path)
    assert snap.run_kind == "experimental"
    assert snap.validation_overrides == []


@pytest.mark.asyncio
async def test_reading_1_7_file_backfills_lims_project_subfields(
    writer: CreationWriter, creation_path: Path
) -> None:
    """1.7 -> 1.8 added ``lims_project.source`` and
    ``lims_project.cache_freshness_at_use``. Spec §11.9.2: the reader
    backfills them with the documented defaults (``"live"`` / ``None``)."""
    on_wire = {
        "schema_version": "1.7",
        "created_at": "2026-01-01T00:00:00Z",
        "created_by": "old-user",
        "level": "run",
        "run_kind": "experimental",
        "lims_project": {
            "uid": "x",
            "short_id": "PROJ-OLD",
            "name_at_creation": "old proj",
        },
        "template": {
            "name": "old_tpl",
            "version": "1.0",
            "source_path": "x",
            "run_scope": "both",
        },
        "variables": {},
        "paths": {"local": "/x", "nas": "//y"},
    }
    creation_path.write_bytes(msgspec_json.encode(on_wire))
    snap = await writer.read_creation_snapshot(creation_path)
    assert snap.lims_project.source == "live"
    assert snap.lims_project.cache_freshness_at_use is None


@pytest.mark.asyncio
async def test_writer_bumps_schema_version_on_mutation_of_old_minor(
    writer: CreationWriter, creation_path: Path
) -> None:
    """Spec §11.9.3 rule 3: on mutation, an older-minor file is rewritten
    at the writer's current minor."""
    on_wire = {
        "schema_version": "1.7",
        "created_at": "2026-01-01T00:00:00Z",
        "created_by": "old-user",
        "level": "run",
        "run_kind": "experimental",
        "lims_project": {
            "uid": "x",
            "short_id": "PROJ-OLD",
            "name_at_creation": "old proj",
        },
        "template": {
            "name": "old_tpl",
            "version": "1.0",
            "source_path": "x",
            "run_scope": "both",
        },
        "variables": {},
        "paths": {"local": "/x", "nas": "//y"},
    }
    creation_path.write_bytes(msgspec_json.encode(on_wire))

    def mutator(p: CreationJson) -> CreationJson:
        p.sync_status = "synced"
        return p

    await writer.update_creation_atomic(creation_path, mutator)
    on_disk = msgspec_json.decode(creation_path.read_bytes(), type=dict[str, object])
    assert on_disk["schema_version"] == CREATION_JSON_VERSION


@pytest.mark.asyncio
async def test_writing_always_emits_current_schema_version(
    writer: CreationWriter, creation_path: Path
) -> None:
    """Spec §11.9.3 rule 1: a writer at version R always writes R for new files."""
    payload = _build_minimal_payload(schema_version="1.5")
    await writer.write_creation(creation_path, payload)
    on_disk = msgspec_json.decode(creation_path.read_bytes(), type=dict[str, object])
    assert on_disk["schema_version"] == CREATION_JSON_VERSION


# ---------------------------------------------------------------------------
# select_active_overrides -- pure-function matching algorithm (§11.3)
# ---------------------------------------------------------------------------


def test_select_active_overrides_returns_empty_for_empty_input() -> None:
    assert select_active_overrides([]) == []


def test_select_active_overrides_keeps_unrevoked_unexpired_entries() -> None:
    entries = [
        override_entry_to_dict(
            OverrideEntry(
                id="aa",
                problem_class="unresolved_placeholder_token",
                operator="op",
                recorded_at="2026-04-18T09:14:22Z",
                reason="r",
            )
        ),
    ]
    active = select_active_overrides(entries)
    assert len(active) == 1
    assert active[0]["id"] == "aa"


def test_select_active_overrides_drops_revoked_entry() -> None:
    entries = [
        override_entry_to_dict(
            OverrideEntry(
                id="aa",
                problem_class="leftover_jinja_marker",
                operator="op",
                recorded_at="2026-04-18T09:14:22Z",
                reason="r",
            )
        ),
        tombstone_entry_to_dict(
            TombstoneEntryStruct(
                id="bb",
                revokes="aa",
                operator="op",
                recorded_at="2026-04-18T10:00:00Z",
                reason="revoked",
            )
        ),
    ]
    active = select_active_overrides(entries)
    assert active == []


def test_select_active_overrides_drops_expired_entry() -> None:
    yesterday = (datetime.now(tz=UTC) - timedelta(days=1)).isoformat()
    tomorrow = (datetime.now(tz=UTC) + timedelta(days=1)).isoformat()
    entries = [
        override_entry_to_dict(
            OverrideEntry(
                id="aa",
                problem_class="leftover_jinja_marker",
                operator="op",
                recorded_at="2026-04-18T09:14:22Z",
                reason="expired",
                expires_at=yesterday,
            )
        ),
        override_entry_to_dict(
            OverrideEntry(
                id="bb",
                problem_class="reserved_filesystem_name",
                operator="op",
                recorded_at="2026-04-18T09:14:22Z",
                reason="future",
                expires_at=tomorrow,
            )
        ),
    ]
    active = select_active_overrides(entries)
    active_ids = {e["id"] for e in active}
    assert active_ids == {"bb"}


def test_select_active_overrides_uses_explicit_now_for_determinism() -> None:
    """The matching algorithm uses ``now=`` for deterministic tests; the
    same fixture should produce different results at different ``now``s."""
    expires_at = "2026-04-18T12:00:00Z"
    entry = override_entry_to_dict(
        OverrideEntry(
            id="aa",
            problem_class="leftover_jinja_marker",
            operator="op",
            recorded_at="2026-04-18T09:14:22Z",
            reason="r",
            expires_at=expires_at,
        )
    )
    before = datetime(2026, 4, 18, 10, 0, 0, tzinfo=UTC)
    after = datetime(2026, 4, 18, 14, 0, 0, tzinfo=UTC)
    assert select_active_overrides([entry], now=before) == [entry]
    assert select_active_overrides([entry], now=after) == []


def test_select_active_overrides_ignores_tombstone_for_unknown_id() -> None:
    """Spec §11.3: orphan tombstones are logged WARN but have no effect."""
    entries = [
        tombstone_entry_to_dict(
            TombstoneEntryStruct(
                id="bb",
                revokes="missing-id",
                operator="op",
                recorded_at="2026-04-18T10:00:00Z",
                reason="revokes nothing",
            )
        ),
        override_entry_to_dict(
            OverrideEntry(
                id="cc",
                problem_class="leftover_jinja_marker",
                operator="op",
                recorded_at="2026-04-18T09:14:22Z",
                reason="r",
            )
        ),
    ]
    active = select_active_overrides(entries)
    assert {e["id"] for e in active} == {"cc"}


def test_select_active_overrides_treats_malformed_expires_at_as_expired() -> None:
    """Fail-safe: a malformed ``expires_at`` re-engages the gate."""
    entry = {
        "id": "aa",
        "problem_class": "unresolved_placeholder_token",
        "operator": "op",
        "recorded_at": "2026-04-18T09:14:22Z",
        "reason": "r",
        "revoked": False,
        "expires_at": "this is not a timestamp",
    }
    assert select_active_overrides([entry]) == []


# ---------------------------------------------------------------------------
# Round-trip: encoding nested structs (plugins_applied isolation block)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_round_trip_preserves_plugins_applied_with_isolation(
    writer: CreationWriter, creation_path: Path
) -> None:
    payload = _build_minimal_payload(
        plugins_applied=[
            PluginApplied(
                plugin="xlsx_field_filler",
                version="0.3.1",
                files_affected=["metadata.xlsx"],
                status="success",
                isolation=PluginIsolation(duration_ms=412, exit_code=0, peak_memory_mb=38),
            ),
        ]
    )
    await writer.write_creation(creation_path, payload)
    snap = await writer.read_creation_snapshot(creation_path)
    assert len(snap.plugins_applied) == 1
    assert snap.plugins_applied[0].isolation is not None
    assert snap.plugins_applied[0].isolation.duration_ms == 412


# ---------------------------------------------------------------------------
# msgspec.ValidationError surfaces for missing required fields on read
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reading_file_with_missing_required_field_raises_validation_error(
    writer: CreationWriter, creation_path: Path
) -> None:
    creation_path.write_bytes(b'{"schema_version": "1.8"}')
    with pytest.raises(msgspec.ValidationError):
        await writer.read_creation_snapshot(creation_path)
