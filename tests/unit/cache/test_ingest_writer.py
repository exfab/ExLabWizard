"""Unit tests for ``exlab_wizard.cache.ingest_writer``.

Covers Backend Spec §13.3 (state machine), §13.4 (on-disk shape), and
§4.4.5 (atomic + locked write contract). Each transition path is verified
end-to-end against an actual file on ``tmp_path`` so the atomic-replace
codepath is exercised.

Constructing an ``IngestJson`` requires Agent B's
``exlab_wizard.api.schemas`` -- if those Structs are not yet present these
tests fail at import time, which is the expected behaviour until the
parallel agents are integrated.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import msgspec
import pytest

from exlab_wizard.api.schemas import IngestJson
from exlab_wizard.cache.ingest_writer import IngestWriter
from exlab_wizard.constants import INGEST_JSON_NAME, INGEST_JSON_VERSION, IngestState

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HOST = "labpc-04"


def _make_payload(**overrides: object) -> IngestJson:
    """Construct a minimal valid ``IngestJson`` for testing.

    The starting payload sits in the ``staging`` state with an empty
    ``history`` list. Tests that need a specific shape can override any
    field via ``**overrides``.
    """
    base: dict[str, object] = {
        "schema_version": INGEST_JSON_VERSION,
        "project_name": "Cortex Q3 Pilot",
        "equipment_id": "CONFOCAL_01",
        "run_kind": "experimental",
        "run_path": "CONFOCAL_01/PROJ-0042/Run_2026-04-17T14-32-00",
        "transport": "smb_mount",
        "current_state": IngestState.STAGING.value,
        "history": [
            {
                "state": IngestState.STAGING.value,
                "at": "2026-04-17T14:35:00Z",
                "host": _HOST,
            },
        ],
    }
    base.update(overrides)
    return msgspec.convert(base, type=IngestJson)


def _ingest_path(tmp_path: Path) -> Path:
    return tmp_path / ".exlab-wizard" / INGEST_JSON_NAME


# ---------------------------------------------------------------------------
# write_ingest / read_ingest
# ---------------------------------------------------------------------------


async def test_write_ingest_creates_a_valid_v1_1_file(tmp_path: Path) -> None:
    writer = IngestWriter()
    path = _ingest_path(tmp_path)
    payload = _make_payload()

    await writer.write_ingest(path, payload)

    assert path.exists()
    raw: dict = msgspec.json.decode(path.read_bytes())
    assert raw["schema_version"] == INGEST_JSON_VERSION
    assert raw["schema_version"].startswith("1.")
    assert raw["current_state"] == IngestState.STAGING.value
    assert raw["project_name"] == "Cortex Q3 Pilot"
    assert raw["equipment_id"] == "CONFOCAL_01"
    assert raw["run_kind"] == "experimental"
    assert raw["transport"] == "smb_mount"
    assert isinstance(raw["history"], list)


async def test_write_ingest_creates_parent_directories(tmp_path: Path) -> None:
    writer = IngestWriter()
    nested = tmp_path / "a" / "b" / ".exlab-wizard" / INGEST_JSON_NAME
    payload = _make_payload()

    await writer.write_ingest(nested, payload)

    assert nested.exists()


async def test_read_ingest_round_trips(tmp_path: Path) -> None:
    writer = IngestWriter()
    path = _ingest_path(tmp_path)
    payload = _make_payload()
    await writer.write_ingest(path, payload)

    roundtrip = await writer.read_ingest(path)

    assert roundtrip.schema_version == payload.schema_version
    assert roundtrip.project_name == payload.project_name
    assert roundtrip.equipment_id == payload.equipment_id
    assert roundtrip.run_kind == payload.run_kind
    assert roundtrip.run_path == payload.run_path
    assert roundtrip.transport == payload.transport
    assert roundtrip.current_state == payload.current_state
    assert roundtrip.history == payload.history


# ---------------------------------------------------------------------------
# append_state_transition -- valid forward transitions
# ---------------------------------------------------------------------------


async def test_append_state_transition_updates_current_state_and_history(
    tmp_path: Path,
) -> None:
    writer = IngestWriter()
    path = _ingest_path(tmp_path)
    await writer.write_ingest(path, _make_payload())

    new_payload = await writer.append_state_transition(
        path,
        IngestState.COMPLETE,
        host=_HOST,
        files_received=142,
        bytes_received=48_293_847_234,
    )

    assert new_payload.current_state == IngestState.COMPLETE.value
    assert len(new_payload.history) == 2
    last = new_payload.history[-1]
    assert last["state"] == IngestState.COMPLETE.value
    assert last["host"] == _HOST
    assert "at" in last
    # The new entry must be persisted to disk, not just held in memory.
    on_disk = await writer.read_ingest(path)
    assert on_disk.current_state == new_payload.current_state
    assert on_disk.history == new_payload.history


async def test_append_complete_records_files_and_bytes(tmp_path: Path) -> None:
    writer = IngestWriter()
    path = _ingest_path(tmp_path)
    await writer.write_ingest(path, _make_payload())

    new_payload = await writer.append_state_transition(
        path,
        IngestState.COMPLETE,
        host=_HOST,
        files_received=142,
        bytes_received=48_293_847_234,
    )

    last = new_payload.history[-1]
    assert last["files_received"] == 142
    assert last["bytes_received"] == 48_293_847_234


async def test_append_sync_verified_records_nas_path_and_checksum(
    tmp_path: Path,
) -> None:
    writer = IngestWriter()
    path = _ingest_path(tmp_path)
    # Walk the state machine forward to sync_queued before transitioning to
    # sync_verified -- the writer rejects non-forward transitions.
    payload = _make_payload(
        current_state=IngestState.SYNC_QUEUED.value,
        history=[
            {"state": IngestState.STAGING.value, "at": "t0", "host": _HOST},
            {"state": IngestState.COMPLETE.value, "at": "t1", "host": _HOST},
            {"state": IngestState.SYNC_QUEUED.value, "at": "t2", "host": _HOST},
        ],
    )
    await writer.write_ingest(path, payload)

    new_payload = await writer.append_state_transition(
        path,
        IngestState.SYNC_VERIFIED,
        host=_HOST,
        nas_path="//nas01/lab/CONFOCAL_01/PROJ-0042/Run_2026-04-17T14-32-00",
        checksum_file=".exlab-wizard/checksums.sha256",
    )

    last = new_payload.history[-1]
    assert last["state"] == IngestState.SYNC_VERIFIED.value
    assert last["nas_path"] == "//nas01/lab/CONFOCAL_01/PROJ-0042/Run_2026-04-17T14-32-00"
    assert last["checksum_file"] == ".exlab-wizard/checksums.sha256"


async def test_append_intermediate_states_do_not_carry_complete_extras(
    tmp_path: Path,
) -> None:
    """``files_received`` is only meaningful for the ``complete`` transition.

    The writer drops the extras silently for non-matching states so callers
    don't accidentally inject misleading audit entries.
    """
    writer = IngestWriter()
    path = _ingest_path(tmp_path)
    payload = _make_payload(
        current_state=IngestState.COMPLETE.value,
        history=[
            {"state": IngestState.STAGING.value, "at": "t0", "host": _HOST},
            {"state": IngestState.COMPLETE.value, "at": "t1", "host": _HOST},
        ],
    )
    await writer.write_ingest(path, payload)

    new_payload = await writer.append_state_transition(
        path,
        IngestState.SYNC_QUEUED,
        host=_HOST,
        files_received=99,  # non-matching extras must be dropped
        bytes_received=99,
    )
    last = new_payload.history[-1]
    assert "files_received" not in last
    assert "bytes_received" not in last


# ---------------------------------------------------------------------------
# append_state_transition -- backward / illegal transitions raise ValueError
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("start_state", "bad_target"),
    [
        # cleared -> staging is the canonical "going backward" example from
        # the spec text in §13.3.
        (IngestState.CLEARED, IngestState.STAGING),
        (IngestState.SYNC_VERIFIED, IngestState.STAGING),
        (IngestState.COMPLETE, IngestState.STAGING),
        # Skipping a state forward is also illegal -- only single-step
        # forward transitions from §13.3 are permitted.
        (IngestState.STAGING, IngestState.SYNC_QUEUED),
        (IngestState.STAGING, IngestState.SYNC_VERIFIED),
        (IngestState.STAGING, IngestState.CLEARED),
        (IngestState.COMPLETE, IngestState.SYNC_VERIFIED),
        (IngestState.SYNC_QUEUED, IngestState.CLEARED),
        # Self-loops are rejected (no-op transitions are not allowed).
        (IngestState.STAGING, IngestState.STAGING),
        (IngestState.COMPLETE, IngestState.COMPLETE),
    ],
)
async def test_append_state_transition_rejects_illegal_transitions(
    tmp_path: Path,
    start_state: IngestState,
    bad_target: IngestState,
) -> None:
    writer = IngestWriter()
    path = _ingest_path(tmp_path)
    payload = _make_payload(
        current_state=start_state.value,
        history=[{"state": start_state.value, "at": "t0", "host": _HOST}],
    )
    await writer.write_ingest(path, payload)

    with pytest.raises(
        ValueError,
        match=r"illegal state transition|Invalid ingest state transition",
    ):
        await writer.append_state_transition(path, bad_target, host=_HOST)


async def test_append_state_transition_does_not_mutate_file_on_failure(
    tmp_path: Path,
) -> None:
    writer = IngestWriter()
    path = _ingest_path(tmp_path)
    await writer.write_ingest(path, _make_payload())
    before = path.read_bytes()

    with pytest.raises(ValueError):
        await writer.append_state_transition(
            path,
            IngestState.SYNC_VERIFIED,  # not reachable from staging in one hop
            host=_HOST,
        )

    assert path.read_bytes() == before


# ---------------------------------------------------------------------------
# History preservation across multiple sequential transitions
# ---------------------------------------------------------------------------


async def test_history_is_preserved_across_five_sequential_transitions(
    tmp_path: Path,
) -> None:
    """Walk the entire state machine and check no entry is dropped."""
    writer = IngestWriter()
    path = _ingest_path(tmp_path)
    await writer.write_ingest(path, _make_payload())

    # staging -> complete
    await writer.append_state_transition(
        path,
        IngestState.COMPLETE,
        host=_HOST,
        files_received=10,
        bytes_received=1024,
    )
    # complete -> sync_queued
    await writer.append_state_transition(path, IngestState.SYNC_QUEUED, host=_HOST)
    # sync_queued -> sync_verified
    await writer.append_state_transition(
        path,
        IngestState.SYNC_VERIFIED,
        host=_HOST,
        nas_path="//nas/run",
        checksum_file=".exlab-wizard/checksums.sha256",
    )
    # sync_verified -> cleared
    final = await writer.append_state_transition(path, IngestState.CLEARED, host=_HOST)

    assert final.current_state == IngestState.CLEARED.value
    states_in_history = [h["state"] for h in final.history]
    assert states_in_history == [
        IngestState.STAGING.value,  # from initial payload
        IngestState.COMPLETE.value,
        IngestState.SYNC_QUEUED.value,
        IngestState.SYNC_VERIFIED.value,
        IngestState.CLEARED.value,
    ]
    # Inspect the on-disk file to make sure each entry survived the
    # tmp+replace cycle and not just the in-memory return value.
    on_disk = await writer.read_ingest(path)
    assert [h["state"] for h in on_disk.history] == states_in_history


# ---------------------------------------------------------------------------
# Concurrent appends do not lose entries (FileLock contract)
# ---------------------------------------------------------------------------


async def test_concurrent_appends_serialize_via_filelock(tmp_path: Path) -> None:
    """Five concurrent tasks attempt to transition the same file.

    Only one transition is legal from any single source state, so we expect
    one task to succeed (staging -> complete) and the rest to raise
    ``ValueError`` because the file is already past ``staging``. The point
    of the test is that the lock prevents corruption: regardless of
    interleaving, the file ends in a coherent state with exactly one
    ``complete`` entry appended (no torn writes, no lost entries).
    """
    writer = IngestWriter()
    path = _ingest_path(tmp_path)
    await writer.write_ingest(path, _make_payload())

    async def attempt() -> str:
        try:
            await writer.append_state_transition(
                path,
                IngestState.COMPLETE,
                host=_HOST,
                files_received=1,
                bytes_received=1,
            )
        except ValueError:
            return "rejected"
        else:
            return "ok"

    results = await asyncio.gather(*(attempt() for _ in range(5)))
    assert results.count("ok") == 1
    assert results.count("rejected") == 4

    final = await writer.read_ingest(path)
    assert final.current_state == IngestState.COMPLETE.value
    # Exactly one complete entry got appended; no duplicates and no torn
    # writes left the file in an indecipherable state.
    complete_entries = [h for h in final.history if h["state"] == IngestState.COMPLETE.value]
    assert len(complete_entries) == 1


async def test_concurrent_walk_through_state_machine_records_all_transitions(
    tmp_path: Path,
) -> None:
    """Five tasks each take one valid forward step, dispatched in parallel.

    Each task waits to find the file in *its* expected source state before
    transitioning. The test asserts the final file has exactly five
    additional history entries (one per task) in the correct order, proving
    the FileLock ordered the read-mutate-write cycles correctly.
    """
    writer = IngestWriter()
    path = _ingest_path(tmp_path)
    await writer.write_ingest(path, _make_payload())

    async def wait_then_transition(
        from_state: IngestState,
        to_state: IngestState,
    ) -> None:
        # Spin until the file's current_state matches our source. Yields
        # control so other tasks can advance the file. Bounded retry count
        # so a stuck test fails fast rather than hanging.
        for _ in range(200):
            current = await writer.read_ingest(path)
            if current.current_state == from_state.value:
                break
            await asyncio.sleep(0.01)
        await writer.append_state_transition(path, to_state, host=_HOST)

    # The five forward transitions covering the full lifecycle.
    transitions = [
        (IngestState.STAGING, IngestState.COMPLETE),
        (IngestState.COMPLETE, IngestState.SYNC_QUEUED),
        (IngestState.SYNC_QUEUED, IngestState.SYNC_VERIFIED),
        (IngestState.SYNC_VERIFIED, IngestState.CLEARED),
    ]
    await asyncio.gather(
        *(wait_then_transition(src, dst) for src, dst in transitions),
    )

    final = await writer.read_ingest(path)
    assert final.current_state == IngestState.CLEARED.value
    # Initial staging entry from _make_payload() + 4 transitions = 5 entries.
    assert [h["state"] for h in final.history] == [
        IngestState.STAGING.value,
        IngestState.COMPLETE.value,
        IngestState.SYNC_QUEUED.value,
        IngestState.SYNC_VERIFIED.value,
        IngestState.CLEARED.value,
    ]


# ---------------------------------------------------------------------------
# Schema-major mismatch on read (§11.9.2)
# ---------------------------------------------------------------------------


async def test_read_ingest_raises_on_schema_major_mismatch(tmp_path: Path) -> None:
    from exlab_wizard.errors import SchemaMajorMismatchError

    writer = IngestWriter()
    path = _ingest_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Hand-write a v2.0 ingest.json -- the writer is at v1.x, so reading
    # this file MUST raise SchemaMajorMismatchError per §11.9.2.
    path.write_bytes(
        msgspec.json.encode(
            {
                "schema_version": "2.0",
                "project_name": "x",
                "equipment_id": "X",
                "run_kind": "experimental",
                "run_path": "X/x/Run_x",
                "transport": "smb_mount",
                "current_state": "staging",
                "history": [],
            },
        ),
    )

    with pytest.raises(SchemaMajorMismatchError) as info:
        await writer.read_ingest(path)
    assert info.value.expected_major == 1
    assert info.value.found == "2.0"


async def test_append_state_transition_raises_on_schema_major_mismatch(
    tmp_path: Path,
) -> None:
    from exlab_wizard.errors import SchemaMajorMismatchError

    writer = IngestWriter()
    path = _ingest_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        msgspec.json.encode(
            {
                "schema_version": "2.0",
                "project_name": "x",
                "equipment_id": "X",
                "run_kind": "experimental",
                "run_path": "X/x/Run_x",
                "transport": "smb_mount",
                "current_state": "staging",
                "history": [],
            },
        ),
    )

    with pytest.raises(SchemaMajorMismatchError):
        await writer.append_state_transition(path, IngestState.COMPLETE, host=_HOST)


# ---------------------------------------------------------------------------
# default_host()
# ---------------------------------------------------------------------------


def test_default_host_returns_socket_gethostname() -> None:
    """The convenience exposed for tests + orchestrator bootstrap matches
    ``socket.gethostname()`` exactly."""
    import socket

    from exlab_wizard.cache.ingest_writer import default_host

    assert default_host() == socket.gethostname()
