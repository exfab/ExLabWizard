"""Unit tests for ``exlab_wizard.cache.sync_state_writer``.

Covers the operator-free per-file NAS sync design (2026-05-21):
``sync_state.json`` read/write, the freely-mutable per-file ``upsert``,
the ``keep_local`` toggle, ``mark_cleared``, and the pure ``rollup_state``
derivation. Each mutating method is exercised end-to-end against a real
file on ``tmp_path`` so the atomic-replace codepath runs.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import msgspec
import pytest

from exlab_wizard.api.schemas import FileSyncRecord, SyncStateJson
from exlab_wizard.cache.sync_state_writer import SyncStateWriter
from exlab_wizard.constants import SYNC_STATE_FILENAME, SYNC_STATE_JSON_VERSION, RunSyncState
from exlab_wizard.errors import SchemaMajorMismatchError
from exlab_wizard.paths import cache_dir


def _state_path(tmp_path: Path) -> Path:
    return cache_dir(tmp_path) / SYNC_STATE_FILENAME


# ---------------------------------------------------------------------------
# read -- absent file
# ---------------------------------------------------------------------------


async def test_read_when_absent_returns_empty_state(tmp_path: Path) -> None:
    writer = SyncStateWriter()

    state = await writer.read(tmp_path)

    assert isinstance(state, SyncStateJson)
    assert state.schema_version == SYNC_STATE_JSON_VERSION
    assert state.cleared_at is None
    assert state.files == {}
    # Reading must not create the file.
    assert not _state_path(tmp_path).exists()


# ---------------------------------------------------------------------------
# upsert_file -- create then update
# ---------------------------------------------------------------------------


async def test_upsert_file_creates_a_record(tmp_path: Path) -> None:
    writer = SyncStateWriter()

    state = await writer.upsert_file(
        tmp_path,
        "data/scan.tif",
        synced_signature=(1024, 17_000_000_000),
        verified_at="2026-05-21T10:00:00Z",
    )

    assert _state_path(tmp_path).exists()
    record = state.files["data/scan.tif"]
    assert record.synced_signature == (1024, 17_000_000_000)
    assert record.verified_at == "2026-05-21T10:00:00Z"
    assert record.keep_local is False
    # Persisted to disk, not just held in memory.
    on_disk = await writer.read(tmp_path)
    assert on_disk.files["data/scan.tif"].synced_signature == (1024, 17_000_000_000)


async def test_upsert_file_updates_existing_record(tmp_path: Path) -> None:
    writer = SyncStateWriter()
    await writer.upsert_file(tmp_path, "data/scan.tif", synced_signature=(10, 20))

    updated = await writer.upsert_file(
        tmp_path,
        "data/scan.tif",
        synced_signature=(99, 100),
        verified_at="2026-05-21T12:00:00Z",
    )

    record = updated.files["data/scan.tif"]
    assert record.synced_signature == (99, 100)
    assert record.verified_at == "2026-05-21T12:00:00Z"
    # Still exactly one record -- update, not append.
    assert list(updated.files) == ["data/scan.tif"]


async def test_upsert_file_preserves_keep_local(tmp_path: Path) -> None:
    writer = SyncStateWriter()
    await writer.set_keep_local(tmp_path, "data/scan.tif", True)

    state = await writer.upsert_file(
        tmp_path,
        "data/scan.tif",
        synced_signature=(5, 6),
        verified_at="2026-05-21T13:00:00Z",
    )

    record = state.files["data/scan.tif"]
    assert record.keep_local is True
    assert record.synced_signature == (5, 6)
    assert record.verified_at == "2026-05-21T13:00:00Z"


# ---------------------------------------------------------------------------
# set_keep_local -- toggle and create-if-absent
# ---------------------------------------------------------------------------


async def test_set_keep_local_creates_record_if_absent(tmp_path: Path) -> None:
    writer = SyncStateWriter()

    state = await writer.set_keep_local(tmp_path, "data/scan.tif", True)

    record = state.files["data/scan.tif"]
    assert record.keep_local is True
    assert record.synced_signature is None
    assert record.verified_at is None


async def test_set_keep_local_toggles_and_preserves_sync_fields(tmp_path: Path) -> None:
    writer = SyncStateWriter()
    await writer.upsert_file(
        tmp_path,
        "data/scan.tif",
        synced_signature=(7, 8),
        verified_at="2026-05-21T14:00:00Z",
    )

    enabled = await writer.set_keep_local(tmp_path, "data/scan.tif", True)
    assert enabled.files["data/scan.tif"].keep_local is True
    # Sync fields untouched by the toggle.
    assert enabled.files["data/scan.tif"].synced_signature == (7, 8)
    assert enabled.files["data/scan.tif"].verified_at == "2026-05-21T14:00:00Z"

    disabled = await writer.set_keep_local(tmp_path, "data/scan.tif", False)
    assert disabled.files["data/scan.tif"].keep_local is False
    assert disabled.files["data/scan.tif"].synced_signature == (7, 8)


# ---------------------------------------------------------------------------
# mark_cleared
# ---------------------------------------------------------------------------


async def test_mark_cleared_sets_cleared_at(tmp_path: Path) -> None:
    writer = SyncStateWriter()
    await writer.upsert_file(tmp_path, "data/scan.tif", synced_signature=(1, 2))

    state = await writer.mark_cleared(tmp_path)

    assert state.cleared_at is not None
    assert state.cleared_at.endswith("Z")
    # Files survive cleanup (tombstone visibility).
    assert "data/scan.tif" in state.files
    # Persisted.
    on_disk = await writer.read(tmp_path)
    assert on_disk.cleared_at == state.cleared_at


async def test_mark_cleared_on_absent_file_creates_state(tmp_path: Path) -> None:
    writer = SyncStateWriter()

    state = await writer.mark_cleared(tmp_path)

    assert state.cleared_at is not None
    assert _state_path(tmp_path).exists()


async def test_mutators_create_missing_exlab_wizard_dir(tmp_path: Path) -> None:
    """Each blocking mutator mkdir's the run's ``.exlab-wizard/`` cache dir.

    S2 robustness: ``atomic_write_bytes`` / ``FileLock`` do not create the
    parent dir, so a mutator targeting a run with no ``.exlab-wizard/``
    would raise. ``_ensure_cache_dir`` makes every mutator self-healing.
    """
    writer = SyncStateWriter()
    # set_keep_local on a run dir whose cache dir does not exist.
    upsert_run = tmp_path / "run_a"
    upsert_run.mkdir()
    keep_run = tmp_path / "run_b"
    keep_run.mkdir()
    clear_run = tmp_path / "run_c"
    clear_run.mkdir()

    await writer.upsert_file(upsert_run, "data.bin", synced_signature=(1, 2))
    await writer.set_keep_local(keep_run, "data.bin", True)
    await writer.mark_cleared(clear_run)

    assert _state_path(upsert_run).exists()
    assert _state_path(keep_run).exists()
    assert _state_path(clear_run).exists()
    assert writer.read_sync(keep_run).files["data.bin"].keep_local is True


# ---------------------------------------------------------------------------
# rollup_state -- pure derivation
# ---------------------------------------------------------------------------


def test_rollup_state_syncing_when_empty() -> None:
    state = SyncStateJson(schema_version=SYNC_STATE_JSON_VERSION)
    assert SyncStateWriter.rollup_state(state) is RunSyncState.SYNCING


def test_rollup_state_syncing_when_some_unverified() -> None:
    state = SyncStateJson(
        schema_version=SYNC_STATE_JSON_VERSION,
        files={
            "a": FileSyncRecord(verified_at="2026-05-21T10:00:00Z"),
            "b": FileSyncRecord(verified_at=None),
        },
    )
    assert SyncStateWriter.rollup_state(state) is RunSyncState.SYNCING


def test_rollup_state_synced_when_all_verified() -> None:
    state = SyncStateJson(
        schema_version=SYNC_STATE_JSON_VERSION,
        files={
            "a": FileSyncRecord(verified_at="2026-05-21T10:00:00Z"),
            "b": FileSyncRecord(verified_at="2026-05-21T11:00:00Z"),
        },
    )
    assert SyncStateWriter.rollup_state(state) is RunSyncState.SYNCED


def test_rollup_state_cleared_takes_precedence_over_unverified() -> None:
    state = SyncStateJson(
        schema_version=SYNC_STATE_JSON_VERSION,
        cleared_at="2026-05-21T15:00:00Z",
        files={"a": FileSyncRecord(verified_at=None)},
    )
    assert SyncStateWriter.rollup_state(state) is RunSyncState.CLEARED


# ---------------------------------------------------------------------------
# Struct round-trip
# ---------------------------------------------------------------------------


def test_sync_state_json_round_trips_through_msgspec() -> None:
    state = SyncStateJson(
        schema_version=SYNC_STATE_JSON_VERSION,
        cleared_at="2026-05-21T16:00:00Z",
        files={
            "data/scan.tif": FileSyncRecord(
                synced_signature=(2048, 99_000_000_000),
                verified_at="2026-05-21T10:00:00Z",
                keep_local=True,
            ),
            "data/raw.bin": FileSyncRecord(),
        },
    )

    blob = msgspec.json.encode(state)
    decoded = msgspec.json.decode(blob, type=SyncStateJson)

    assert decoded == state
    assert decoded.files["data/scan.tif"].synced_signature == (2048, 99_000_000_000)
    assert decoded.files["data/scan.tif"].keep_local is True
    assert decoded.files["data/raw.bin"].synced_signature is None


# ---------------------------------------------------------------------------
# Schema-major mismatch on read (§11.9.2)
# ---------------------------------------------------------------------------


async def test_read_raises_on_schema_major_mismatch(tmp_path: Path) -> None:
    """A file at a different schema major than the writer is rejected.

    The writer is at v1.x; a hand-written v2.0 ``sync_state.json`` MUST
    raise ``SchemaMajorMismatchError`` rather than silently partial-parse.
    """
    path = _state_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        msgspec.json.encode(
            {
                "schema_version": "2.0",
                "cleared_at": None,
                "files": {},
            },
        ),
    )

    writer = SyncStateWriter()
    with pytest.raises(SchemaMajorMismatchError) as info:
        await writer.read(tmp_path)
    assert info.value.expected_major == 1
    assert info.value.found == "2.0"


# ---------------------------------------------------------------------------
# Concurrent upserts do not lose records (FileLock contract)
# ---------------------------------------------------------------------------


async def test_concurrent_upserts_on_distinct_files_both_survive(tmp_path: Path) -> None:
    """Two concurrent ``upsert_file`` calls on the same run, distinct paths.

    The ``FileLock`` serializes the read-mutate-write cycle so neither
    upsert clobbers the other's record -- both must be present afterward.
    """
    writer = SyncStateWriter()

    await asyncio.gather(
        writer.upsert_file(
            tmp_path,
            "data/scan_a.tif",
            synced_signature=(10, 20),
            verified_at="2026-05-22T10:00:00Z",
        ),
        writer.upsert_file(
            tmp_path,
            "data/scan_b.tif",
            synced_signature=(30, 40),
            verified_at="2026-05-22T11:00:00Z",
        ),
    )

    state = await writer.read(tmp_path)
    assert set(state.files) == {"data/scan_a.tif", "data/scan_b.tif"}
    assert state.files["data/scan_a.tif"].synced_signature == (10, 20)
    assert state.files["data/scan_b.tif"].synced_signature == (30, 40)
