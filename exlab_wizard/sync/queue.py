"""Durable SQLite-backed sync-job queue. Backend Spec §7.1.1, §7.1.2, §7.1.5.

The queue is a single SQLite file (``{state_dir}/sync_queue.db``) that
holds one row per NAS-sync job. Jobs persist across server restarts so
in-flight work survives process exits; on startup, ``RUNNING`` jobs are
re-queued and ``AWAITING_VERIFY`` jobs are re-verified.

State machine (§7.1.2)::

    QUEUED -> RUNNING -> AWAITING_VERIFY -> VERIFIED -> CLEANUP_ELIGIBLE -> CLEANED
       \\         \\              \\              \\
        FAILED <- FAILED  <- FAILED <- FAILED

Backoff (§7.1.5): ``30s, 2m, 8m, 30m, 2h``. After 5 failed attempts the
job becomes terminal ``FAILED``. Auth failures and ``local_file_vanished``
go straight to ``FAILED`` with no backoff.

The schema below mirrors the API contract in the Phase 10 brief:

.. code-block:: sql

    CREATE TABLE jobs (
        id TEXT PRIMARY KEY,
        run_path TEXT NOT NULL UNIQUE,
        equipment_id TEXT NOT NULL,
        state TEXT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0,
        last_attempt_at TEXT,
        next_attempt_at TEXT,
        last_error TEXT,
        verify_passes INTEGER NOT NULL DEFAULT 0,
        verified_at TEXT,
        enqueued_at TEXT NOT NULL,
        nas_path TEXT
    )
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path

import aiosqlite

from exlab_wizard.logging import get_logger
from exlab_wizard.utils.time import dt_to_iso, utc_now_iso, utc_now_or

__all__ = [
    "BACKOFF_SCHEDULE_SECONDS",
    "MAX_ATTEMPTS",
    "SyncJobRow",
    "SyncJobState",
    "SyncQueue",
]

_log = get_logger(__name__)


class SyncJobState(StrEnum):
    """State machine for a sync job. Backend Spec §7.1.2."""

    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_VERIFY = "awaiting_verify"
    VERIFIED = "verified"
    CLEANUP_ELIGIBLE = "cleanup_eligible"
    CLEANED = "cleaned"
    FAILED = "failed"


# Retry backoff sequence per §7.1.5: 30s, 2m, 8m, 30m, 2h. After 5 attempts
# the job becomes terminal FAILED.
BACKOFF_SCHEDULE_SECONDS: tuple[int, ...] = (30, 120, 480, 1800, 7200)
MAX_ATTEMPTS: int = 5


_TERMINAL_STATES: frozenset[SyncJobState] = frozenset({SyncJobState.FAILED, SyncJobState.CLEANED})


_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    run_path TEXT NOT NULL UNIQUE,
    equipment_id TEXT NOT NULL,
    state TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TEXT,
    next_attempt_at TEXT,
    last_error TEXT,
    verify_passes INTEGER NOT NULL DEFAULT 0,
    verified_at TEXT,
    enqueued_at TEXT NOT NULL,
    nas_path TEXT
)
"""


_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs(state)",
    "CREATE INDEX IF NOT EXISTS idx_jobs_next_attempt ON jobs(next_attempt_at)",
)


@dataclass(frozen=True, slots=True)
class SyncJobRow:
    """One row in the ``jobs`` table. Backend Spec §7.1.1."""

    id: str
    run_path: str
    equipment_id: str
    state: SyncJobState
    attempts: int = 0
    last_attempt_at: str | None = None
    next_attempt_at: str | None = None
    last_error: str | None = None
    verify_passes: int = 0
    verified_at: str | None = None
    enqueued_at: str = ""
    nas_path: str | None = None


def _row_to_job(row: aiosqlite.Row | tuple) -> SyncJobRow:
    """Materialize an ``aiosqlite.Row`` (or tuple) into a :class:`SyncJobRow`."""
    return SyncJobRow(
        id=row[0],
        run_path=row[1],
        equipment_id=row[2],
        state=SyncJobState(row[3]),
        attempts=row[4] or 0,
        last_attempt_at=row[5],
        next_attempt_at=row[6],
        last_error=row[7],
        verify_passes=row[8] or 0,
        verified_at=row[9],
        enqueued_at=row[10] or "",
        nas_path=row[11],
    )


def compute_next_attempt_at(*, attempts_after: int, now: datetime | None = None) -> str | None:
    """Return the ISO timestamp of the next retry attempt.

    ``attempts_after`` is the value of ``attempts`` AFTER the current
    failure has been recorded. ``None`` means "no further attempts" --
    either because the job is terminal or because the schedule has been
    exhausted.
    """
    if attempts_after < 1 or attempts_after > MAX_ATTEMPTS:
        return None
    delay_seconds = BACKOFF_SCHEDULE_SECONDS[attempts_after - 1]
    moment = utc_now_or(now) + timedelta(seconds=delay_seconds)
    return dt_to_iso(moment)


class SyncQueue:
    """Async SQLite-backed durable sync queue. Backend Spec §7.1.1.

    Use :meth:`init` once at application startup; the database file is
    created on demand. After init, all CRUD methods are coroutines.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    @property
    def db_path(self) -> Path:
        """The on-disk path of the queue database file."""
        return self._db_path

    async def init(self) -> None:
        """Open the connection, ensure the schema, and replay in-flight rows.

        Replay semantics (§7.1.2): any ``RUNNING`` row at startup gets
        downgraded to ``QUEUED`` (the worker died mid-transfer); any
        ``AWAITING_VERIFY`` row is left as-is so the verifier picks it up.
        """
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(str(self._db_path))
        # Use WAL mode so concurrent readers do not block writers; this is
        # the standard recommendation for SQLite-backed queues.
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute(_CREATE_TABLE_SQL)
        for stmt in _INDEX_SQL:
            await self._conn.execute(stmt)
        # Re-queue jobs that were RUNNING when the previous process exited.
        await self._conn.execute(
            "UPDATE jobs SET state = ? WHERE state = ?",
            (SyncJobState.QUEUED.value, SyncJobState.RUNNING.value),
        )
        await self._conn.commit()
        _log.debug("SyncQueue init at %s", self._db_path)

    async def close(self) -> None:
        """Close the underlying connection (idempotent)."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    def _require_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            msg = "SyncQueue.init() must be called before use"
            raise RuntimeError(msg)
        return self._conn

    async def _require_job(self, job_id: str) -> SyncJobRow:
        """Return the row for ``job_id`` or raise ``ValueError``.

        Centralizes the "look up a job by id, raise on miss" pattern that
        :meth:`transition`, :meth:`record_failure`, and
        :meth:`reset_to_queued` all need.
        """
        existing = await self.get_by_id(job_id)
        if existing is None:
            msg = f"unknown job_id {job_id!r}"
            raise ValueError(msg)
        return existing

    # ----------------------------------------------------------- CRUD

    async def insert(
        self,
        *,
        run_path: Path,
        equipment_id: str,
        nas_path: str | None = None,
        job_id: str | None = None,
    ) -> SyncJobRow:
        """Insert a new ``QUEUED`` row for ``run_path``.

        Raises :class:`aiosqlite.IntegrityError` (via the UNIQUE constraint
        on ``run_path``) if a row already exists for the same path.
        """
        conn = self._require_conn()
        row = SyncJobRow(
            id=job_id or str(uuid.uuid4()),
            run_path=str(run_path),
            equipment_id=equipment_id,
            state=SyncJobState.QUEUED,
            enqueued_at=utc_now_iso(),
            nas_path=nas_path,
        )
        await conn.execute(
            """
            INSERT INTO jobs (
                id, run_path, equipment_id, state, attempts,
                last_attempt_at, next_attempt_at, last_error,
                verify_passes, verified_at, enqueued_at, nas_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row.id,
                row.run_path,
                row.equipment_id,
                row.state.value,
                row.attempts,
                row.last_attempt_at,
                row.next_attempt_at,
                row.last_error,
                row.verify_passes,
                row.verified_at,
                row.enqueued_at,
                row.nas_path,
            ),
        )
        await conn.commit()
        return row

    async def get_by_id(self, job_id: str) -> SyncJobRow | None:
        """Return the row with the given ``job_id`` or ``None``."""
        conn = self._require_conn()
        cursor = await conn.execute(
            "SELECT * FROM jobs WHERE id = ?",
            (job_id,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row is None:
            return None
        return _row_to_job(row)

    async def get_by_run_path(self, run_path: Path) -> SyncJobRow | None:
        """Return the row whose ``run_path`` matches, or ``None``."""
        conn = self._require_conn()
        cursor = await conn.execute(
            "SELECT * FROM jobs WHERE run_path = ?",
            (str(run_path),),
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row is None:
            return None
        return _row_to_job(row)

    async def list_in_state(self, state: SyncJobState) -> list[SyncJobRow]:
        """Return every row currently in ``state`` ordered by ``enqueued_at``."""
        conn = self._require_conn()
        cursor = await conn.execute(
            "SELECT * FROM jobs WHERE state = ? ORDER BY enqueued_at",
            (state.value,),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [_row_to_job(r) for r in rows]

    async def list_all(self) -> list[SyncJobRow]:
        """Return every row in the queue ordered by ``enqueued_at``."""
        conn = self._require_conn()
        cursor = await conn.execute("SELECT * FROM jobs ORDER BY enqueued_at")
        rows = await cursor.fetchall()
        await cursor.close()
        return [_row_to_job(r) for r in rows]

    # ----------------------------------------------------------- transitions

    async def transition(
        self,
        job_id: str,
        new_state: SyncJobState,
        *,
        last_error: str | None = None,
        increment_attempts: bool = False,
        increment_verify_passes: bool = False,
        verified_at: str | None = None,
        next_attempt_at: str | None = None,
        last_attempt_at: str | None = None,
        nas_path: str | None = None,
    ) -> SyncJobRow:
        """Transition a job to ``new_state`` and patch the auxiliary columns.

        The patch is one ``UPDATE`` statement so either every column moves
        or none do. Raises :class:`ValueError` if the job is missing.
        """
        existing = await self._require_job(job_id)

        new_attempts = existing.attempts + (1 if increment_attempts else 0)
        new_verify_passes = existing.verify_passes + (1 if increment_verify_passes else 0)

        updated = replace(
            existing,
            state=new_state,
            attempts=new_attempts,
            last_attempt_at=last_attempt_at
            if last_attempt_at is not None
            else existing.last_attempt_at,
            next_attempt_at=next_attempt_at
            if next_attempt_at is not None
            else existing.next_attempt_at,
            last_error=last_error if last_error is not None else existing.last_error,
            verify_passes=new_verify_passes,
            verified_at=verified_at if verified_at is not None else existing.verified_at,
            nas_path=nas_path if nas_path is not None else existing.nas_path,
        )

        conn = self._require_conn()
        await conn.execute(
            """
            UPDATE jobs SET
                state = ?,
                attempts = ?,
                last_attempt_at = ?,
                next_attempt_at = ?,
                last_error = ?,
                verify_passes = ?,
                verified_at = ?,
                nas_path = ?
            WHERE id = ?
            """,
            (
                updated.state.value,
                updated.attempts,
                updated.last_attempt_at,
                updated.next_attempt_at,
                updated.last_error,
                updated.verify_passes,
                updated.verified_at,
                updated.nas_path,
                updated.id,
            ),
        )
        await conn.commit()
        return updated

    async def record_failure(
        self,
        job_id: str,
        error: str,
        *,
        terminal: bool = False,
        now: datetime | None = None,
    ) -> SyncJobRow:
        """Record a transport failure on ``job_id``.

        If ``terminal`` is True (auth failure, local file vanished) the
        job goes straight to ``FAILED`` with no backoff. Otherwise:

        - increment ``attempts``
        - if ``attempts >= MAX_ATTEMPTS``: terminal ``FAILED``.
        - else: stay in ``QUEUED`` with ``next_attempt_at`` per backoff.
        """
        existing = await self._require_job(job_id)

        now = utc_now_or(now)
        last_attempt_iso = dt_to_iso(now)
        if terminal:
            return await self.transition(
                job_id,
                SyncJobState.FAILED,
                last_error=error,
                last_attempt_at=last_attempt_iso,
                next_attempt_at="",
            )

        new_attempts = existing.attempts + 1
        if new_attempts >= MAX_ATTEMPTS:
            return await self.transition(
                job_id,
                SyncJobState.FAILED,
                increment_attempts=True,
                last_error=error,
                last_attempt_at=last_attempt_iso,
                next_attempt_at="",
            )
        next_iso = compute_next_attempt_at(attempts_after=new_attempts, now=now) or ""
        return await self.transition(
            job_id,
            SyncJobState.QUEUED,
            increment_attempts=True,
            last_error=error,
            last_attempt_at=last_attempt_iso,
            next_attempt_at=next_iso,
        )

    async def reset_to_queued(self, job_id: str) -> SyncJobRow:
        """Reset a ``FAILED`` job back to ``QUEUED`` for a manual retry.

        Per §7.1.5 the Problems-tab Retry action re-enqueues a failed job.
        ``attempts`` and ``last_error`` are cleared so the backoff schedule
        starts fresh.
        """
        # Side-effect: raises ``ValueError`` if the job id is unknown.
        await self._require_job(job_id)
        conn = self._require_conn()
        await conn.execute(
            """
            UPDATE jobs SET
                state = ?,
                attempts = 0,
                last_attempt_at = NULL,
                next_attempt_at = NULL,
                last_error = NULL
            WHERE id = ?
            """,
            (SyncJobState.QUEUED.value, job_id),
        )
        await conn.commit()
        return await self.get_by_id(job_id)  # type: ignore[return-value]

    async def delete(self, job_id: str) -> None:
        """Remove a job row entirely. Used by the cleanup reaper after CLEANED."""
        conn = self._require_conn()
        await conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        await conn.commit()

    @staticmethod
    def is_terminal(state: SyncJobState) -> bool:
        """Return True if the state is a terminal state (no further work)."""
        return state in _TERMINAL_STATES
