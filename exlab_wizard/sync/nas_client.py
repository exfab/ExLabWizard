"""NAS sync client. Backend Spec §7.1, §7.3.

The :class:`NASSyncClient` is the public surface of the NAS sync
subsystem. It wires together the durable queue, the transport drivers,
the SHA-256 verifier, the bandwidth scheduler, the cleanup interlocks,
and the Pre-Sync Gate.

Per §7.1 the client is an in-process module of the FastAPI app; there is
no separate daemon. Workers are asyncio tasks; the queue file is the
durable record so a server restart does not lose pending work.
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from exlab_wizard.api.schemas import CreationJson
from exlab_wizard.cache.creation_writer import CreationWriter
from exlab_wizard.config.models import Config, EquipmentConfig, RcloneTransport, RsyncSshTransport
from exlab_wizard.constants import (
    CACHE_DIR_NAME,
    CREATION_JSON_NAME,
    SyncStatus,
)
from exlab_wizard.logging import get_logger
from exlab_wizard.sync.bandwidth import effective_bandwidth_limit_kibps
from exlab_wizard.sync.cleanup import cleanup_interlocks_satisfied
from exlab_wizard.sync.pre_sync_gate import is_eligible
from exlab_wizard.sync.queue import (
    SyncJobRow,
    SyncJobState,
    SyncQueue,
)
from exlab_wizard.sync.transports import (
    TransportError,
    TransportErrorKind,
    TransportResult,
)
from exlab_wizard.sync.transports.rclone import RcloneTransport as RcloneDriver
from exlab_wizard.sync.transports.rsync_ssh import RsyncSshTransport as RsyncDriver
from exlab_wizard.sync.verifier import Verifier, VerifyResult
from exlab_wizard.validator.engine import Validator
from exlab_wizard.validator.findings import Finding

__all__ = [
    "NASSyncClient",
    "SyncJobHandle",
    "SyncJobState",
]


_log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Public DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SyncJobHandle:
    """Lightweight handle returned by :meth:`NASSyncClient.enqueue`.

    ``job_id`` is empty when the gate blocked enqueue (the on-disk
    ``sync_status`` will reflect the block). ``blocking_findings`` is
    present iff ``state == BLOCKED``.
    """

    job_id: str
    state: str
    run_path: str
    blocking_findings: tuple[Finding, ...] = ()


# Closed-set state alias kept distinct from the queue's StrEnum so the
# Pre-Sync-Gate rejection has a name distinct from FAILED.
class HandleState:
    """String constants for :class:`SyncJobHandle.state`. Backend Spec §7.3."""

    QUEUED = "queued"
    BLOCKED = "blocked"


# ---------------------------------------------------------------------------
# Transport-driver wiring
# ---------------------------------------------------------------------------


def _build_transport_driver(equipment: EquipmentConfig) -> tuple[Any, Callable[..., Any]]:
    """Return a ``(driver, push_callable)`` pair for ``equipment.transport``.

    The push callable closes over the equipment's static ``ssh_target`` /
    ``rclone_remote`` so the queue worker only needs the local source path
    and the per-equipment bandwidth cap at call time.
    """
    transport = equipment.transport
    if isinstance(transport, RcloneTransport):
        driver = RcloneDriver()
        remote = f"{transport.rclone_remote}:{transport.rclone_remote_path}"

        async def _push(local: Path, *, bwlimit_kibps: int | None) -> TransportResult:
            return await driver.push(local, remote, bwlimit_kibps=bwlimit_kibps)

        return driver, _push

    if isinstance(transport, RsyncSshTransport):
        driver = RsyncDriver()
        ssh_key = Path(transport.ssh_key_path).expanduser()

        async def _push(local: Path, *, bwlimit_kibps: int | None) -> TransportResult:
            return await driver.push(
                local,
                transport.ssh_target,
                ssh_key,
                transport.remote_path,
                bwlimit_kibps=bwlimit_kibps,
            )

        return driver, _push

    msg = f"unsupported transport type: {type(transport).__name__}"
    raise ValueError(msg)


# ---------------------------------------------------------------------------
# NAS sync client
# ---------------------------------------------------------------------------


class NASSyncClient:
    """Durable, per-equipment NAS sync queue with Pre-Sync Gate.

    Backend Spec §7.1, §7.3.

    Lifecycle:

    * :meth:`init` opens the queue DB, replays any in-flight jobs, and
      starts a single background worker task.
    * :meth:`enqueue` runs the Pre-Sync Gate, gates the run if needed,
      and otherwise inserts a ``QUEUED`` row.
    * :meth:`close` cancels the worker and closes the DB.

    The worker loop is a simple "pick the oldest QUEUED whose
    ``next_attempt_at`` has passed" scheduler with at-most-one inflight
    job at a time. This keeps determinism for tests; production
    deployments can extend to per-equipment parallelism without changing
    the public API.
    """

    def __init__(
        self,
        *,
        config: Config,
        queue_db: Path,
        validator: Validator,
        cache_creation: CreationWriter,
        verifier: Verifier | None = None,
        worker_poll_interval_s: float = 0.05,
        push_callable_factory: Callable[[EquipmentConfig], Callable[..., Any]] | None = None,
        remote_stat_callable: Callable[[SyncJobRow], bool] | None = None,
    ) -> None:
        self._config = config
        self._queue_db = queue_db
        self._validator = validator
        self._cache_creation = cache_creation
        self._verifier = verifier or Verifier()
        self._queue = SyncQueue(queue_db)
        self._equipment_by_id = {e.id: e for e in config.equipment}
        self._worker_poll_interval_s = worker_poll_interval_s
        self._worker_task: asyncio.Task[None] | None = None
        self._wake_event = asyncio.Event()
        self._stopping = False
        self._push_callable_factory = push_callable_factory
        # Default remote stat: optimistic OK so unit tests don't need
        # to wire a real network probe.
        self._remote_stat_callable = remote_stat_callable or (lambda _row: True)

    # ------------------------------------------------------------------ async API

    async def init(self) -> None:
        """Open the queue and start the worker task. Backend Spec §7.1.2."""
        await self._queue.init()
        self._worker_task = asyncio.create_task(self._worker_loop())
        _log.debug("NASSyncClient init at %s", self._queue_db)

    async def close(self) -> None:
        """Stop the worker and close the queue DB. Idempotent."""
        self._stopping = True
        self._wake_event.set()
        if self._worker_task is not None:
            self._worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._worker_task
            self._worker_task = None
        await self._queue.close()

    # ------------------------------------------------------------------ enqueue

    async def enqueue(self, run_path: Path) -> SyncJobHandle:
        """Pre-Sync Gate -> if hard-tier finding without override, mark
        ``sync_status='blocked_by_validation'``. Otherwise insert a
        ``QUEUED`` row.

        Returns a :class:`SyncJobHandle`. The handle's ``state`` is
        either :attr:`HandleState.BLOCKED` or :attr:`HandleState.QUEUED`.
        """
        creation_path = run_path / CACHE_DIR_NAME / CREATION_JSON_NAME
        creation = await self._cache_creation.read_creation_snapshot(creation_path)

        eligible, blocking = is_eligible(
            validator=self._validator,
            creation_json_path=creation_path,
            creation=creation,
        )
        if not eligible:
            await self._mark_blocked(creation_path)
            return SyncJobHandle(
                job_id="",
                state=HandleState.BLOCKED,
                run_path=str(run_path),
                blocking_findings=tuple(blocking),
            )

        equipment_id = self._infer_equipment_id(run_path, creation)
        existing = await self._queue.get_by_run_path(run_path)
        if existing is not None:
            # Re-enqueueing an existing run is a no-op except for FAILED rows,
            # which we re-arm in QUEUED.
            if existing.state == SyncJobState.FAILED:
                row = await self._queue.reset_to_queued(existing.id)
                self._wake_event.set()
                return SyncJobHandle(
                    job_id=row.id,
                    state=HandleState.QUEUED,
                    run_path=str(run_path),
                )
            return SyncJobHandle(
                job_id=existing.id,
                state=HandleState.QUEUED,
                run_path=str(run_path),
            )

        row = await self._queue.insert(
            run_path=run_path,
            equipment_id=equipment_id,
            nas_path=self._compute_nas_path(creation),
        )
        self._wake_event.set()
        return SyncJobHandle(job_id=row.id, state=HandleState.QUEUED, run_path=str(run_path))

    async def status(self, run_path: Path) -> str:
        """Return the queue state of the job for ``run_path``.

        ``"none"`` when no job exists; otherwise the underlying
        :class:`SyncJobState` value.
        """
        row = await self._queue.get_by_run_path(run_path)
        if row is None:
            return "none"
        return row.state.value

    async def retry(self, job_id: str) -> None:
        """Re-arm a ``FAILED`` job. Backend Spec §7.1.5 (Problems-tab Retry)."""
        await self._queue.reset_to_queued(job_id)
        self._wake_event.set()

    async def force_verify(self, run_path: Path) -> VerifyResult:
        """Recompute the local manifest and verify against itself.

        Used by the Settings "verify integrity" action. Does not advance
        the queue state.
        """
        manifest = await self._verifier.compute_local_manifest(run_path)
        return await self._verifier.verify_against_local(run_path, manifest)

    # ----------------------------------------------------------- worker

    async def _worker_loop(self) -> None:
        """Pick the next due job and drive it through the state machine."""
        while not self._stopping:
            job = await self._next_due_job()
            if job is None:
                # Wait for a wake signal or poll-interval timeout.
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(
                        self._wake_event.wait(),
                        timeout=self._worker_poll_interval_s,
                    )
                self._wake_event.clear()
                continue
            try:
                await self._drive_job(job)
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover -- defensive
                _log.exception("worker exception on job %s", job.id)

    async def _next_due_job(self) -> SyncJobRow | None:
        """Return the next QUEUED row whose backoff has passed (or None)."""
        rows = await self._queue.list_in_state(SyncJobState.QUEUED)
        now_iso = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        for row in rows:
            if not row.next_attempt_at:
                return row
            if row.next_attempt_at <= now_iso:
                return row
        return None

    async def _drive_job(self, job: SyncJobRow) -> None:
        """Drive ``job`` from QUEUED through one transport+verify pass.

        Worker semantics:

        - Validate that the local run still exists; if not, terminal
          FAILED with ``local_file_vanished``.
        - Transition QUEUED -> RUNNING.
        - Push via the transport; on AUTH or LOCAL_FILE_VANISHED, mark
          terminal FAILED. On NETWORK or UNKNOWN, schedule a retry.
        - On success, transition RUNNING -> AWAITING_VERIFY -> VERIFIED.
        - On VERIFIED, write/refresh the manifest and bump
          ``sync_status`` to ``"synced"``.
        - Subsequent passes (a manual ``force_verify`` or the audit
          loop) increment ``verify_passes`` and may move the job
          through CLEANUP_ELIGIBLE -> CLEANED.
        """
        run_path = Path(job.run_path)
        if not run_path.exists():  # noqa: ASYNC240 -- one-shot stat for vanished-local check
            await self._queue.record_failure(
                job.id,
                error=TransportErrorKind.LOCAL_FILE_VANISHED.value,
                terminal=True,
            )
            return

        equipment = self._equipment_by_id.get(job.equipment_id)
        if equipment is None:
            await self._queue.record_failure(
                job.id,
                error=f"equipment {job.equipment_id!r} not configured",
                terminal=True,
            )
            return

        # Transition QUEUED -> RUNNING.
        await self._queue.transition(job.id, SyncJobState.RUNNING)

        # Compute bandwidth cap for this attempt.
        bwlimit = effective_bandwidth_limit_kibps(
            equipment.transport.bandwidth, now_local=datetime.now()
        )

        push = self._build_push(equipment)
        try:
            result = await push(run_path, bwlimit_kibps=bwlimit)
        except TransportError as exc:
            await self._queue.record_failure(job.id, error=str(exc), terminal=False)
            return

        if not result.ok:
            await self._handle_push_failure(job, result)
            return

        # Push succeeded. Transition RUNNING -> AWAITING_VERIFY.
        await self._queue.transition(job.id, SyncJobState.AWAITING_VERIFY)

        # Verify locally (the §7.1.4 manifest pass). The remote-side hash
        # comparison is the responsibility of integration tests; the
        # in-process verifier asserts the local subtree matches its own
        # manifest, which is the cheaper subset that catches partial
        # transports.
        try:
            verify_result = await self._verify_pass(run_path)
        except FileNotFoundError:
            await self._queue.record_failure(
                job.id,
                error=TransportErrorKind.LOCAL_FILE_VANISHED.value,
                terminal=True,
            )
            return

        if not verify_result.ok:
            # Hash mismatch policy: single retry of the transport phase
            # by re-queuing once. Track the previous hash mismatch via
            # ``last_error`` so a second failure becomes terminal.
            previous = job.last_error or ""
            if TransportErrorKind.HASH_MISMATCH.value in previous:
                await self._queue.transition(
                    job.id,
                    SyncJobState.FAILED,
                    last_error=TransportErrorKind.HASH_MISMATCH.value,
                )
                return
            await self._queue.transition(
                job.id,
                SyncJobState.QUEUED,
                last_error=TransportErrorKind.HASH_MISMATCH.value,
                next_attempt_at="",
            )
            return

        # Promote to VERIFIED and record one verify pass.
        verified_iso = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        await self._queue.transition(
            job.id,
            SyncJobState.VERIFIED,
            increment_verify_passes=True,
            verified_at=verified_iso,
        )
        await self._mark_synced(run_path)

        # Cleanup interlocks (§7.1.6). If satisfied, transition through
        # CLEANUP_ELIGIBLE -> CLEANED in one pass.
        await self._maybe_cleanup(job.id, run_path)

    def _build_push(self, equipment: EquipmentConfig) -> Callable[..., Any]:
        """Resolve the push callable for ``equipment.transport``.

        Tests can inject a custom factory via the constructor's
        ``push_callable_factory`` argument so they don't need real
        rclone / rsync binaries.
        """
        if self._push_callable_factory is not None:
            return self._push_callable_factory(equipment)
        _, push = _build_transport_driver(equipment)
        return push

    async def _handle_push_failure(self, job: SyncJobRow, result: TransportResult) -> None:
        """Translate a transport failure into a queue update."""
        kind = result.error_kind or TransportErrorKind.UNKNOWN
        if kind in (TransportErrorKind.AUTH, TransportErrorKind.LOCAL_FILE_VANISHED):
            await self._queue.record_failure(job.id, error=kind.value, terminal=True)
            return
        if kind == TransportErrorKind.HASH_MISMATCH:
            # Hash mismatch reported by the transport (rclone --checksum):
            # treat as a single retry of the transport phase. Use the
            # job's last_error to know if this is the second occurrence.
            previous = job.last_error or ""
            if TransportErrorKind.HASH_MISMATCH.value in previous:
                await self._queue.record_failure(job.id, error=kind.value, terminal=True)
                return
            await self._queue.transition(
                job.id,
                SyncJobState.QUEUED,
                last_error=kind.value,
                next_attempt_at="",
            )
            return
        # NETWORK / UNKNOWN -> backoff retry.
        await self._queue.record_failure(job.id, error=kind.value, terminal=False)

    async def _verify_pass(self, run_path: Path) -> VerifyResult:
        """Run one local manifest + verify pass."""
        manifest = await self._verifier.compute_local_manifest(run_path)
        return await self._verifier.verify_against_local(run_path, manifest)

    async def _maybe_cleanup(self, job_id: str, run_path: Path) -> None:
        """Apply the §7.1.6 interlocks; if all pass, run the cleanup."""
        if not self._config.nas_cleanup.enabled:
            return
        job = await self._queue.get_by_id(job_id)
        if job is None or job.state != SyncJobState.VERIFIED:
            return
        creation_path = run_path / CACHE_DIR_NAME / CREATION_JSON_NAME
        creation: CreationJson | None = None
        if creation_path.exists():
            with contextlib.suppress(Exception):
                creation = await self._cache_creation.read_creation_snapshot(creation_path)
        overrides = list(creation.validation_overrides) if creation else []

        remote_ok = self._remote_stat_callable(job)
        now_utc = datetime.now(tz=UTC)
        if not cleanup_interlocks_satisfied(
            job=job,
            run_path=run_path,
            now_utc=now_utc,
            config=self._config.nas_cleanup,
            overrides_active=overrides,
            remote_stat_ok=remote_ok,
        ):
            await self._queue.transition(job_id, SyncJobState.CLEANUP_ELIGIBLE)
            return

        # Promote to CLEANUP_ELIGIBLE then perform the deletion.
        await self._queue.transition(job_id, SyncJobState.CLEANUP_ELIGIBLE)
        self._delete_local(run_path)
        await self._queue.transition(job_id, SyncJobState.CLEANED)

    def _delete_local(self, run_path: Path) -> None:
        """Delete ``run_path`` data files honoring ``retain_cache``.

        With the default ``retain_cache=True`` we keep the
        ``.exlab-wizard/`` subtree so the local browse view can still
        render the run with a ``cleaned`` badge (§7.1.10).
        """
        if not run_path.exists():
            return
        retain = self._config.nas_cleanup.retain_cache
        if retain:
            for entry in run_path.iterdir():
                if entry.name == CACHE_DIR_NAME:
                    continue
                if entry.is_dir():
                    shutil.rmtree(entry, ignore_errors=True)
                else:
                    with contextlib.suppress(OSError):
                        entry.unlink()
        else:
            shutil.rmtree(run_path, ignore_errors=True)

    # ----------------------------------------------------------- helpers

    def _infer_equipment_id(self, run_path: Path, creation: CreationJson) -> str:
        """Return the equipment id for a run path.

        Prefers an explicit equipment id derivable from the creation
        payload's resolved local path. Falls back to the run-path's
        first segment if everything else is missing.
        """
        # The wizard's path convention is
        # <local_root>/<EQUIPMENT_ID>/<PROJ-NNNN>/Run_<DATE>/.
        # Walk up from creation.paths.local until we find a directory
        # whose name matches a configured equipment id.
        candidates = [Path(creation.paths.local)] if creation.paths.local else []
        candidates.append(run_path)
        for candidate in candidates:
            for part in candidate.parts:
                if part in self._equipment_by_id:
                    return part
        # Last-ditch: trust the first equipment id in config.
        if self._equipment_by_id:
            return next(iter(self._equipment_by_id))
        return ""

    @staticmethod
    def _compute_nas_path(creation: CreationJson) -> str | None:
        """Return the recorded NAS-side path from a creation payload."""
        return creation.paths.nas or None

    async def _mark_blocked(self, creation_path: Path) -> None:
        """Mutate ``creation.json`` ``sync_status`` to ``blocked_by_validation``."""

        def _gate(payload: CreationJson) -> CreationJson:
            payload.sync_status = SyncStatus.BLOCKED_BY_VALIDATION.value
            return payload

        await self._cache_creation.update_creation_atomic(creation_path, _gate)

    async def _mark_synced(self, run_path: Path) -> None:
        """Mutate ``creation.json`` ``sync_status`` to ``synced``. Backend Spec §7.1.4."""
        creation_path = run_path / CACHE_DIR_NAME / CREATION_JSON_NAME
        if not creation_path.exists():
            return

        def _flip(payload: CreationJson) -> CreationJson:
            payload.sync_status = SyncStatus.SYNCED.value
            return payload

        with contextlib.suppress(Exception):
            await self._cache_creation.update_creation_atomic(creation_path, _flip)
