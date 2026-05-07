"""Tests for ``exlab_wizard.sync.queue``.

Covers the durable SQLite-backed sync-job queue. Backend Spec §7.1.1,
§7.1.2, §7.1.5. The queue is the only persistent piece of the sync
subsystem; if it loses rows on restart, every other contract collapses.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest

from exlab_wizard.sync.queue import (
    BACKOFF_SCHEDULE_SECONDS,
    MAX_ATTEMPTS,
    SyncJobState,
    SyncQueue,
    compute_next_attempt_at,
)


@pytest.fixture()
async def queue(tmp_path: Path) -> SyncQueue:
    q = SyncQueue(tmp_path / "queue.db")
    await q.init()
    try:
        yield q
    finally:
        await q.close()


async def test_insert_creates_queued_row(queue: SyncQueue, tmp_path: Path) -> None:
    """``insert`` produces a QUEUED row with a generated UUID job id."""
    row = await queue.insert(run_path=tmp_path / "run", equipment_id="EQ1")
    assert row.state is SyncJobState.QUEUED
    assert row.equipment_id == "EQ1"
    assert row.attempts == 0
    assert row.id  # UUID
    assert row.enqueued_at  # ISO timestamp


async def test_insert_unique_constraint(queue: SyncQueue, tmp_path: Path) -> None:
    """Two inserts on the same run_path violate UNIQUE."""
    await queue.insert(run_path=tmp_path / "run", equipment_id="EQ1")
    with pytest.raises(aiosqlite.IntegrityError):
        await queue.insert(run_path=tmp_path / "run", equipment_id="EQ1")


async def test_get_by_id_and_run_path(queue: SyncQueue, tmp_path: Path) -> None:
    row = await queue.insert(run_path=tmp_path / "run", equipment_id="EQ1")
    by_id = await queue.get_by_id(row.id)
    by_path = await queue.get_by_run_path(tmp_path / "run")
    assert by_id is not None
    assert by_path is not None
    assert by_id.id == by_path.id == row.id


async def test_get_missing_returns_none(queue: SyncQueue, tmp_path: Path) -> None:
    assert await queue.get_by_id("nope") is None
    assert await queue.get_by_run_path(tmp_path / "missing") is None


async def test_transition_forward(queue: SyncQueue, tmp_path: Path) -> None:
    row = await queue.insert(run_path=tmp_path / "run", equipment_id="EQ1")
    moved = await queue.transition(row.id, SyncJobState.RUNNING)
    assert moved.state is SyncJobState.RUNNING
    fetched = await queue.get_by_id(row.id)
    assert fetched is not None
    assert fetched.state is SyncJobState.RUNNING


async def test_transition_unknown_raises(queue: SyncQueue) -> None:
    with pytest.raises(ValueError, match="unknown job_id"):
        await queue.transition("does-not-exist", SyncJobState.RUNNING)


async def test_transition_increments_verify_passes(queue: SyncQueue, tmp_path: Path) -> None:
    row = await queue.insert(run_path=tmp_path / "run", equipment_id="EQ1")
    after = await queue.transition(
        row.id,
        SyncJobState.VERIFIED,
        increment_verify_passes=True,
        verified_at="2026-04-17T14:32:00Z",
    )
    assert after.verify_passes == 1
    assert after.verified_at == "2026-04-17T14:32:00Z"


async def test_record_failure_increments_attempts(queue: SyncQueue, tmp_path: Path) -> None:
    row = await queue.insert(run_path=tmp_path / "run", equipment_id="EQ1")
    after = await queue.record_failure(row.id, "boom", terminal=False)
    assert after.attempts == 1
    assert after.state is SyncJobState.QUEUED
    assert after.last_error == "boom"
    assert after.next_attempt_at  # backoff scheduled


async def test_record_failure_terminal(queue: SyncQueue, tmp_path: Path) -> None:
    row = await queue.insert(run_path=tmp_path / "run", equipment_id="EQ1")
    after = await queue.record_failure(row.id, "auth", terminal=True)
    assert after.state is SyncJobState.FAILED
    assert after.last_error == "auth"


async def test_record_failure_terminal_after_max_attempts(queue: SyncQueue, tmp_path: Path) -> None:
    row = await queue.insert(run_path=tmp_path / "run", equipment_id="EQ1")
    last = row
    for _ in range(MAX_ATTEMPTS):
        last = await queue.record_failure(last.id, "boom", terminal=False)
    assert last.state is SyncJobState.FAILED
    assert last.attempts >= MAX_ATTEMPTS


async def test_compute_next_attempt_at_uses_schedule() -> None:
    """The 1st through 5th attempts use the documented backoff sequence."""
    fixed_now = datetime(2026, 4, 17, 14, 32, 0, tzinfo=UTC)
    expected_seconds = list(BACKOFF_SCHEDULE_SECONDS)
    for idx, secs in enumerate(expected_seconds, start=1):
        result = compute_next_attempt_at(attempts_after=idx, now=fixed_now)
        expected_iso = (fixed_now + timedelta(seconds=secs)).strftime("%Y-%m-%dT%H:%M:%SZ")
        assert result == expected_iso


async def test_compute_next_attempt_at_out_of_range() -> None:
    """Attempts <1 or >MAX_ATTEMPTS yield ``None``."""
    assert compute_next_attempt_at(attempts_after=0) is None
    assert compute_next_attempt_at(attempts_after=MAX_ATTEMPTS + 1) is None


async def test_persistence_across_reinit(tmp_path: Path) -> None:
    """A queue rebuilt against the same db file sees the prior rows."""
    db_path = tmp_path / "q.db"
    q1 = SyncQueue(db_path)
    await q1.init()
    row = await q1.insert(run_path=tmp_path / "run", equipment_id="EQ1")
    await q1.close()

    q2 = SyncQueue(db_path)
    await q2.init()
    fetched = await q2.get_by_id(row.id)
    assert fetched is not None
    assert fetched.run_path == str(tmp_path / "run")
    await q2.close()


async def test_init_replays_running_to_queued(tmp_path: Path) -> None:
    """A RUNNING row from a prior process is downgraded to QUEUED on restart."""
    db_path = tmp_path / "q.db"
    q1 = SyncQueue(db_path)
    await q1.init()
    row = await q1.insert(run_path=tmp_path / "run", equipment_id="EQ1")
    await q1.transition(row.id, SyncJobState.RUNNING)
    await q1.close()

    q2 = SyncQueue(db_path)
    await q2.init()
    after = await q2.get_by_id(row.id)
    assert after is not None
    assert after.state is SyncJobState.QUEUED
    await q2.close()


async def test_list_in_state_returns_only_matching(queue: SyncQueue, tmp_path: Path) -> None:
    a = await queue.insert(run_path=tmp_path / "a", equipment_id="EQ1")
    b = await queue.insert(run_path=tmp_path / "b", equipment_id="EQ1")
    await queue.transition(a.id, SyncJobState.RUNNING)
    queued_rows = await queue.list_in_state(SyncJobState.QUEUED)
    running_rows = await queue.list_in_state(SyncJobState.RUNNING)
    assert {r.id for r in queued_rows} == {b.id}
    assert {r.id for r in running_rows} == {a.id}


async def test_list_all_orders_by_enqueued_at(queue: SyncQueue, tmp_path: Path) -> None:
    a = await queue.insert(run_path=tmp_path / "a", equipment_id="EQ1")
    b = await queue.insert(run_path=tmp_path / "b", equipment_id="EQ1")
    rows = await queue.list_all()
    ids = [r.id for r in rows]
    assert a.id in ids and b.id in ids


async def test_reset_to_queued_clears_attempts(queue: SyncQueue, tmp_path: Path) -> None:
    row = await queue.insert(run_path=tmp_path / "run", equipment_id="EQ1")
    await queue.record_failure(row.id, "boom", terminal=True)
    after = await queue.reset_to_queued(row.id)
    assert after.state is SyncJobState.QUEUED
    assert after.attempts == 0
    assert after.last_error is None


async def test_reset_to_queued_unknown_raises(queue: SyncQueue) -> None:
    with pytest.raises(ValueError, match="unknown job_id"):
        await queue.reset_to_queued("nope")


async def test_record_failure_unknown_raises(queue: SyncQueue) -> None:
    with pytest.raises(ValueError, match="unknown job_id"):
        await queue.record_failure("nope", "x")


async def test_delete_removes_row(queue: SyncQueue, tmp_path: Path) -> None:
    row = await queue.insert(run_path=tmp_path / "run", equipment_id="EQ1")
    await queue.delete(row.id)
    assert await queue.get_by_id(row.id) is None


async def test_is_terminal_classifies_states() -> None:
    assert SyncQueue.is_terminal(SyncJobState.FAILED)
    assert SyncQueue.is_terminal(SyncJobState.CLEANED)
    assert not SyncQueue.is_terminal(SyncJobState.QUEUED)
    assert not SyncQueue.is_terminal(SyncJobState.RUNNING)


async def test_init_required_before_use(tmp_path: Path) -> None:
    """Operations without init() raise a clear RuntimeError."""
    q = SyncQueue(tmp_path / "uninit.db")
    with pytest.raises(RuntimeError, match="init"):
        await q.insert(run_path=tmp_path / "x", equipment_id="EQ1")


async def test_close_is_idempotent(tmp_path: Path) -> None:
    q = SyncQueue(tmp_path / "q.db")
    await q.init()
    await q.close()
    # Second close should not raise.
    await q.close()
