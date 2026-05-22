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
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from exlab_wizard.api.schemas import CreationJson
from exlab_wizard.cache.creation_writer import CreationWriter
from exlab_wizard.cache.sync_state_writer import SyncStateWriter
from exlab_wizard.config.models import Config, EquipmentConfig, RcloneTransport, RsyncSshTransport
from exlab_wizard.constants import (
    RunSyncState,
    SyncHandleState,
    SyncStatus,
)
from exlab_wizard.logging import get_logger
from exlab_wizard.paths import cache_dir, creation_json_path
from exlab_wizard.sync.bandwidth import effective_bandwidth_limit_kibps
from exlab_wizard.sync.cleanup import cleanup_interlocks_satisfied
from exlab_wizard.sync.pre_sync_gate import is_eligible
from exlab_wizard.sync.queue import (
    SyncJobRow,
    SyncJobState,
    SyncQueue,
)
from exlab_wizard.sync.run_delete import delete_run_files
from exlab_wizard.sync.transports import (
    TransportError,
    TransportErrorKind,
    TransportResult,
)
from exlab_wizard.sync.transports.rclone import RcloneTransport as RcloneDriver
from exlab_wizard.sync.transports.rsync_ssh import RsyncSshTransport as RsyncDriver
from exlab_wizard.sync.verifier import Verifier, VerifyResult
from exlab_wizard.utils.time import utc_now, utc_now_iso
from exlab_wizard.validator.engine import Validator
from exlab_wizard.validator.findings import Finding

__all__ = [
    "NASSyncClient",
    "SyncJobHandle",
    "SyncJobState",
]


_log = get_logger(__name__)


# Job states that count as "done with this subset" for re-enqueue purposes
# (operator-free per-file NAS sync, 2026-05-21). When ``enqueue`` is called
# with a fresh ``files`` list and the run's existing job is in one of these
# states, the row is re-armed in QUEUED with the new subset -- this is how a
# file modified after a prior verify, or queued onto a permanently-failed
# run, gets re-synced.
_TERMINAL_ENQUEUE_STATES: frozenset[SyncJobState] = frozenset(
    {
        SyncJobState.VERIFIED,
        SyncJobState.CLEANUP_ELIGIBLE,
        SyncJobState.CLEANED,
        SyncJobState.FAILED,
    },
)


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
    state: SyncHandleState
    run_path: str
    blocking_findings: tuple[Finding, ...] = ()


# ---------------------------------------------------------------------------
# Transport-driver wiring
# ---------------------------------------------------------------------------


def _build_transport_driver(equipment: EquipmentConfig) -> tuple[Any, Callable[..., Any]]:
    """Return a ``(driver, push_callable)`` pair for ``equipment.transport``.

    The push callable closes over the equipment's static ``ssh_target`` /
    ``rclone_remote`` so the queue worker only needs the local source
    path and the per-equipment bandwidth cap at call time. Both rclone
    and rsync_ssh push closures nest the remote target under
    ``<remote_path>/<local.name>`` so each run lives in its own subdir
    on the NAS, matching the §7.1.4 hashsum probe layout.

    Stage-mode equipment (Redesign §3.2, ``sync_mode == 'stage'``) has no
    ``transport`` block — the orchestrator owns the NAS sync. The
    EquipmentConfig validator guarantees this function only runs against
    nas-mode equipment.
    """
    transport = equipment.transport
    if transport is None:
        msg = (
            f"equipment {equipment.id!r} has sync_mode "
            f"{equipment.sync_mode.value!r}; the NAS-sync queue only handles "
            f"nas-mode equipment with a configured transport"
        )
        raise ValueError(msg)
    if isinstance(transport, RcloneTransport):
        rclone_driver = RcloneDriver()
        remote_name = transport.rclone_remote
        remote_path = transport.rclone_remote_path

        async def _push_rclone(
            local: Path,
            *,
            bwlimit_kibps: int | None,
            files_from: Path | None = None,
        ) -> TransportResult:
            target = f"{remote_name}:{remote_path}/{local.name}"
            return await rclone_driver.push(
                local, target, bwlimit_kibps=bwlimit_kibps, files_from=files_from
            )

        return rclone_driver, _push_rclone

    if isinstance(transport, RsyncSshTransport):
        rsync_driver = RsyncDriver()
        ssh_key = Path(transport.ssh_key_path).expanduser()
        ssh_target = transport.ssh_target
        remote_path_value = transport.remote_path

        async def _push_rsync(
            local: Path,
            *,
            bwlimit_kibps: int | None,
            files_from: Path | None = None,
        ) -> TransportResult:
            target = f"{remote_path_value}/{local.name}"
            return await rsync_driver.push(
                local,
                ssh_target,
                ssh_key,
                target,
                bwlimit_kibps=bwlimit_kibps,
                files_from=files_from,
            )

        return rsync_driver, _push_rsync

    msg = f"unsupported transport type: {type(transport).__name__}"
    raise ValueError(msg)


def _build_hashsum_callable(
    equipment: EquipmentConfig,
) -> Callable[[Path], Awaitable[dict[str, str]]]:
    """Return a remote-hashsum closure for ``equipment.transport``.

    The closure takes a local run directory and asks the remote-side
    hash probe (``rclone hashsum sha256`` or ``ssh ... sha256sum``) for
    the manifest of ``<remote_path>/<run_dir.name>``. Backend Spec
    §7.1.4 (integrity-in-transit gap closure). Only called for nas-mode
    equipment (Redesign §3.2).
    """
    transport = equipment.transport
    if transport is None:
        msg = (
            f"equipment {equipment.id!r} has sync_mode "
            f"{equipment.sync_mode.value!r}; remote-hashsum probes only "
            f"apply to nas-mode equipment with a configured transport"
        )
        raise ValueError(msg)
    if isinstance(transport, RcloneTransport):
        rclone_driver = RcloneDriver()
        remote_name = transport.rclone_remote
        remote_path = transport.rclone_remote_path

        async def _hashsum_rclone(run_dir: Path) -> dict[str, str]:
            target = f"{remote_name}:{remote_path}/{run_dir.name}"
            return await rclone_driver.hashsum(target)

        return _hashsum_rclone

    if isinstance(transport, RsyncSshTransport):
        rsync_driver = RsyncDriver()
        ssh_key = Path(transport.ssh_key_path).expanduser()
        ssh_target = transport.ssh_target
        remote_path_value = transport.remote_path

        async def _hashsum_rsync(run_dir: Path) -> dict[str, str]:
            run_remote_path = f"{remote_path_value}/{run_dir.name}"
            return await rsync_driver.hashsum(ssh_target, ssh_key, run_remote_path)

        return _hashsum_rsync

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
        sync_state_writer: SyncStateWriter | None = None,
        verifier: Verifier | None = None,
        worker_poll_interval_s: float = 0.05,
        push_callable_factory: Callable[[EquipmentConfig], Callable[..., Any]] | None = None,
        hashsum_callable_factory: (
            Callable[[EquipmentConfig], Callable[[Path], Awaitable[dict[str, str]]]] | None
        ) = None,
        remote_stat_callable: Callable[[SyncJobRow], bool] | None = None,
    ) -> None:
        self._config = config
        self._queue_db = queue_db
        self._validator = validator
        self._cache_creation = cache_creation
        self._sync_state_writer = sync_state_writer or SyncStateWriter()
        self._verifier = verifier or Verifier()
        self._queue = SyncQueue(queue_db)
        self._equipment_by_id = {e.id: e for e in config.equipment}
        self._worker_poll_interval_s = worker_poll_interval_s
        self._worker_task: asyncio.Task[None] | None = None
        self._wake_event = asyncio.Event()
        self._stopping = False
        self._push_callable_factory = push_callable_factory
        self._hashsum_callable_factory = hashsum_callable_factory
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

    async def enqueue(
        self,
        run_path: Path,
        files: list[str] | None = None,
    ) -> SyncJobHandle:
        """Pre-Sync Gate -> if hard-tier finding without override, mark
        ``sync_status='blocked_by_validation'``. Otherwise insert a
        ``QUEUED`` row.

        ``files`` (operator-free per-file NAS sync, 2026-05-21) is the
        per-file subset of run-relative POSIX paths eligible at enqueue
        time; an empty / omitted list means "the whole run".

        The queue holds one row per ``run_path`` (UNIQUE). Re-enqueue
        behaviour:

        * existing job in a **terminal** state (``VERIFIED`` /
          ``CLEANUP_ELIGIBLE`` / ``CLEANED`` / ``FAILED``) **and** a
          non-empty ``files`` list -> reset to ``QUEUED`` carrying the new
          subset. This is how a file modified after a prior verify gets
          re-synced.
        * existing job in a terminal ``VERIFIED`` / ``CLEANUP_ELIGIBLE`` /
          ``CLEANED`` state with an **empty** ``files`` list -> falls
          through to a no-op: there is no subset to re-sync and a
          successfully-verified run is not blindly re-queued. (Only a
          terminal ``FAILED`` row with empty ``files`` is re-armed -- the
          manual-retry branch below.)
        * existing job **active** (``QUEUED`` / ``RUNNING`` /
          ``AWAITING_VERIFY``) -> no-op (newly settled files ride the next
          sweep).
        * existing terminal ``FAILED`` job with no ``files`` -> re-armed
          via ``reset_to_queued`` so the manual-retry contract holds.
        * no existing job -> insert a ``QUEUED`` row with ``files``.

        Returns a :class:`SyncJobHandle`. The handle's ``state`` is
        either :attr:`SyncHandleState.BLOCKED` or :attr:`SyncHandleState.QUEUED`.
        """
        files_tuple: tuple[str, ...] = tuple(files or ())
        creation_path = creation_json_path(run_path)
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
                state=SyncHandleState.BLOCKED,
                run_path=str(run_path),
                blocking_findings=tuple(blocking),
            )

        equipment_id = self._infer_equipment_id(run_path, creation)
        existing = await self._queue.get_by_run_path(run_path)
        if existing is not None:
            if existing.state in _TERMINAL_ENQUEUE_STATES and files_tuple:
                # A file modified after a prior verify (or a permanently
                # failed run carrying a fresh subset): re-arm the row in
                # QUEUED with the new file list.
                row = await self._queue.requeue_with_files(existing.id, files_tuple)
                self._wake_event.set()
                return SyncJobHandle(
                    job_id=row.id,
                    state=SyncHandleState.QUEUED,
                    run_path=str(run_path),
                )
            if existing.state == SyncJobState.FAILED:
                # Manual retry with no fresh subset -- keep the old contract.
                row = await self._queue.reset_to_queued(existing.id)
                self._wake_event.set()
                return SyncJobHandle(
                    job_id=row.id,
                    state=SyncHandleState.QUEUED,
                    run_path=str(run_path),
                )
            # Active job (QUEUED / RUNNING / AWAITING_VERIFY): no-op.
            return SyncJobHandle(
                job_id=existing.id,
                state=SyncHandleState.QUEUED,
                run_path=str(run_path),
            )

        row = await self._queue.insert(
            run_path=run_path,
            equipment_id=equipment_id,
            nas_path=self._compute_nas_path(creation),
            files=files_tuple,
        )
        self._wake_event.set()
        return SyncJobHandle(job_id=row.id, state=SyncHandleState.QUEUED, run_path=str(run_path))

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
        now_iso = utc_now_iso()
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
          terminal FAILED. On HASH_MISMATCH, single retry then terminal.
          On NETWORK or UNKNOWN, schedule a backoff retry.
        - On push success, transition RUNNING -> AWAITING_VERIFY and run
          :meth:`_verify_pass` (local manifest + remote hashsum probe).
        - On verify success, transition to VERIFIED and bump
          ``sync_status`` to ``"synced"``.
        - On verify failure, route by ``VerifyResult.error_kind``: AUTH
          -> terminal FAILED, NETWORK / UNKNOWN -> backoff retry, every
          other case (genuine hash mismatch or unclassified probe error)
          -> single retry then terminal.
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

        # Compute bandwidth cap for this attempt. Redesign §3.2: only
        # nas-mode equipment reach the NAS sync queue (the EquipmentConfig
        # validator guarantees transport is set when sync_mode == 'nas');
        # the None case is defensive.
        assert equipment.transport is not None, (
            f"nas-mode equipment {equipment.id!r} must carry a transport block"
        )
        bwlimit = effective_bandwidth_limit_kibps(
            equipment.transport.bandwidth, now_local=datetime.now()
        )

        # Per-file NAS sync (2026-05-21): when the job carries a file
        # subset, write it to a temp ``--files-from`` list so the transport
        # copies only those paths. An empty ``job.files`` keeps the
        # whole-directory copy.
        push = self._build_push(equipment)
        files_from_path: Path | None = None
        try:
            if job.files:
                files_from_path = self._write_files_from(job.files)
            try:
                result = await push(
                    run_path,
                    bwlimit_kibps=bwlimit,
                    files_from=files_from_path,
                )
            except TransportError as exc:
                await self._queue.record_failure(job.id, error=str(exc), terminal=False)
                return
        finally:
            if files_from_path is not None:
                with contextlib.suppress(OSError):
                    files_from_path.unlink()

        if not result.ok:
            await self._handle_push_failure(job, result)
            return

        # Push succeeded. Transition RUNNING -> AWAITING_VERIFY.
        await self._queue.transition(job.id, SyncJobState.AWAITING_VERIFY)

        # Verify locally (the §7.1.4 manifest pass) and then compare
        # against the remote-derived manifest. The local pass catches
        # partial transports cheaply; the remote pass closes the
        # integrity-in-transit gap and is the reason ``equipment`` flows
        # in here -- the verifier needs the transport-specific hashsum
        # callable. When the job carries a file subset, the verify pass is
        # scoped to that subset.
        include = set(job.files) if job.files else None
        try:
            verify_result = await self._verify_pass(run_path, equipment, include=include)
        except FileNotFoundError:
            await self._queue.record_failure(
                job.id,
                error=TransportErrorKind.LOCAL_FILE_VANISHED.value,
                terminal=True,
            )
            return

        # Per-file verify reconciliation (operator-free per-file NAS sync,
        # design "Failure handling"): credit every file that verified in
        # ``sync_state.json`` -- even when the batch job is otherwise marked
        # failed, so a single bad file does not block the good ones. The
        # job's overall pass/fail (retry/backoff) is decided below from
        # ``verify_result.ok`` exactly as before.
        await self._reconcile_synced_files(run_path, job, verify_result)

        if not verify_result.ok:
            # Spec §7.1.5 retry-class routing for verify failures. The
            # remote hashsum probe may have raised TransportError before
            # the verifier could compare manifests; in that case
            # ``verify_result.error_kind`` carries the transport's
            # classification:
            #
            # - AUTH -- terminal FAILED (configuration problem, no retry).
            # - NETWORK / UNKNOWN -- non-terminal failure with backoff.
            # - Any other case (genuine hash mismatch from
            #   ``verify_against_remote``, or a TransportError raised
            #   without a classified ``error_kind``, e.g. binary spawn
            #   failure) -- the §7.1.5 HASH_MISMATCH single-retry-then-
            #   terminal branch. A spawn failure routed this way means the
            #   worker re-queues once, retries the push+probe, and
            #   terminates FAILED on the second failure; the operator
            #   surfaces the binary-missing reason via ``last_error``.
            kind = verify_result.error_kind
            if kind is TransportErrorKind.AUTH:
                await self._queue.record_failure(
                    job.id,
                    error=TransportErrorKind.AUTH.value,
                    terminal=True,
                )
                return
            if kind in (
                TransportErrorKind.NETWORK,
                TransportErrorKind.UNKNOWN,
            ):
                await self._queue.record_failure(
                    job.id,
                    error=kind.value,
                    terminal=False,
                )
                return
            # Genuine hash mismatch (or unclassified probe failure):
            # single retry of the transport phase by re-queuing once.
            # Track the previous hash mismatch via ``last_error`` so a
            # second failure becomes terminal.
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
        verified_iso = utc_now_iso()
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

    def _build_hashsum(
        self, equipment: EquipmentConfig
    ) -> Callable[[Path], Awaitable[dict[str, str]]]:
        """Resolve the hashsum callable for ``equipment.transport``.

        Tests can inject a custom factory via the constructor's
        ``hashsum_callable_factory`` argument so they don't need real
        rclone / rsync binaries (or network access).
        """
        if self._hashsum_callable_factory is not None:
            return self._hashsum_callable_factory(equipment)
        return _build_hashsum_callable(equipment)

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

    async def _verify_pass(
        self,
        run_path: Path,
        equipment: EquipmentConfig,
        *,
        include: set[str] | None = None,
    ) -> VerifyResult:
        """Run one local manifest + verify pass, then probe the remote.

        The local pass is the cheap pre-check; if the local subtree no
        longer matches its own freshly-computed manifest, we return that
        result immediately without spending a remote round-trip. Otherwise
        we ask the transport for its remote-side manifest and compare via
        :meth:`Verifier.verify_against_remote` (Backend Spec §7.1.4 -- the
        integrity-in-transit gap closure).

        ``include`` (operator-free per-file NAS sync, 2026-05-21) scopes the
        local manifest to a run-relative subset; ``None`` hashes the whole
        run. The remote probe still walks the whole run subtree -- the
        remote-vs-local comparison is keyed on the (possibly subset) local
        manifest, so extra remote keys are simply informational.

        A :class:`TransportError` from the hashsum probe is surfaced as a
        verify failure (``ok=False``) carrying the transport's classified
        ``error_kind``. The §7.1.4 step-2 contract mandates a remote
        SHA-256 walk; silently skipping it on a probe error would let the
        job promote through VERIFIED -> CLEANED without the integrity
        check. ``_drive_job`` keys off ``error_kind`` to route the failure
        through the spec-correct §7.1.5 retry path (AUTH -> terminal,
        NETWORK / UNKNOWN -> backoff, every other case including a
        missing-binary spawn failure -> single retry then terminal).
        """
        manifest = await self._verifier.compute_local_manifest(run_path, include)
        local_result = await self._verifier.verify_against_local(run_path, manifest)
        if not local_result.ok:
            return local_result

        hashsum = self._build_hashsum(equipment)
        try:
            remote_manifest = await hashsum(run_path)
        except TransportError as exc:
            _log.warning("remote hashsum probe failed: %s", exc)
            return VerifyResult(
                ok=False,
                manifest=dict(manifest),
                error_kind=exc.error_kind,
            )
        return self._verifier.verify_against_remote(manifest, remote_manifest)

    async def _reconcile_synced_files(
        self,
        run_path: Path,
        job: SyncJobRow,
        verify_result: VerifyResult,
    ) -> None:
        """Credit every individually-verified file in ``sync_state.json``.

        Operator-free per-file NAS sync design ("Failure handling"): a
        per-run batch job may verify some files and fail others. Every file
        that *did* verify is recorded with its current ``(st_size,
        st_mtime_ns)`` ``synced_signature`` and a ``verified_at`` timestamp
        -- even when the batch job is otherwise routed to a retry / FAILED
        -- so a single bad file does not block crediting the good ones.

        A file counts as verified when it is present in the verify result's
        local manifest and absent from both ``mismatched`` and ``missing``.
        When the remote probe could not run at all (``error_kind`` set)
        nothing is credited -- no file's NAS copy was confirmed.
        """
        if verify_result.error_kind is not None:
            return
        bad = set(verify_result.mismatched) | set(verify_result.missing)
        verified_rel = [rel for rel in verify_result.manifest if rel not in bad]
        if not verified_rel:
            return
        verified_at = utc_now_iso()
        for rel in verified_rel:
            signature = self._file_signature(run_path / rel)
            if signature is None:
                continue
            with contextlib.suppress(Exception):
                await self._sync_state_writer.upsert_file(
                    run_path,
                    rel,
                    synced_signature=signature,
                    verified_at=verified_at,
                )

    @staticmethod
    def _file_signature(path: Path) -> tuple[int, int] | None:
        """Return the ``(st_size, st_mtime_ns)`` signature for ``path``."""
        try:
            stat = path.stat()
        except OSError:
            return None
        return (stat.st_size, stat.st_mtime_ns)

    @staticmethod
    def _write_files_from(files: tuple[str, ...]) -> Path:
        """Write a transport ``--files-from`` list and return its path.

        One run-relative POSIX path per line. The caller is responsible for
        unlinking the temp file once the transport invocation completes.
        """
        handle = tempfile.NamedTemporaryFile(  # noqa: SIM115 -- caller unlinks
            mode="w",
            encoding="utf-8",
            prefix="exlab-files-from-",
            suffix=".txt",
            delete=False,
        )
        try:
            handle.write("\n".join(files) + "\n")
        finally:
            handle.close()
        return Path(handle.name)

    async def _maybe_cleanup(self, job_id: str, run_path: Path) -> None:
        """Apply the §7.1.6 interlocks; if all pass, run the cleanup.

        Operator-free per-file NAS sync design ("Cleanup -- rollup"): with
        per-file sync a job reaching ``VERIFIED`` only means *that job's
        file subset* verified -- the run may still hold unsynced files from
        a later sweep. Cleanup therefore additionally requires the whole-run
        ``sync_state.json`` rollup to be ``SYNCED`` (every tracked file
        verified); a partially-synced run is left for a later pass.
        """
        if not self._config.nas_cleanup.enabled:
            return
        job = await self._queue.get_by_id(job_id)
        if job is None or job.state != SyncJobState.VERIFIED:
            return

        # Whole-run rollup gate: every tracked file must be verified before
        # any local deletion. A job's VERIFIED only covers its own subset.
        sync_state = await self._sync_state_writer.read(run_path)
        rollup = self._sync_state_writer.rollup_state(sync_state)
        if rollup != RunSyncState.SYNCED:
            _log.debug(
                "cleanup deferred: run %s not fully SYNCED (rollup=%s)",
                run_path,
                rollup.value,
            )
            return

        creation_path = creation_json_path(run_path)
        creation: CreationJson | None = None
        if creation_path.exists():
            with contextlib.suppress(Exception):
                creation = await self._cache_creation.read_creation_snapshot(creation_path)
        overrides = list(creation.validation_overrides) if creation else []

        remote_ok = self._remote_stat_callable(job)
        now_utc = utc_now()
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

        # Promote to CLEANUP_ELIGIBLE then perform the deletion. Files the
        # operator flagged ``keep_local`` survive the sweep.
        await self._queue.transition(job_id, SyncJobState.CLEANUP_ELIGIBLE)
        keep_local = {rel for rel, rec in sync_state.files.items() if rec.keep_local}
        self._delete_local(run_path, keep_local)
        await self._mark_cleaned(run_path)
        # Stamp ``cleared_at`` in ``sync_state.json`` so the run rolls up to
        # CLEARED. Skipped when the whole-run ``retain_cache=False`` delete
        # removed the cache directory along with the data files -- there is
        # no surviving record to stamp.
        if cache_dir(run_path).exists():
            await self._sync_state_writer.mark_cleared(run_path)
        await self._queue.transition(job_id, SyncJobState.CLEANED)

    def _delete_local(
        self,
        run_path: Path,
        keep_local: set[str] | None = None,
    ) -> None:
        """Delete ``run_path`` data files honoring ``retain_cache`` and ``keep_local``.

        Thin wrapper over the shared :func:`exlab_wizard.sync.run_delete.delete_run_files`
        helper so the automatic cleanup reaper and the operator-facing
        ``clear_run_dir`` stay in lockstep: ``keep_local`` files survive, the
        ``.exlab-wizard/`` subtree survives (when ``retain_cache``), and
        directory symlinks are never descended into or removed.
        """
        delete_run_files(
            run_path,
            keep_local=keep_local or set(),
            retain_cache=self._config.nas_cleanup.retain_cache,
        )

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
            payload.sync_status = SyncStatus.BLOCKED_BY_VALIDATION
            return payload

        await self._cache_creation.update_creation_atomic(creation_path, _gate)

    async def _mark_synced(self, run_path: Path) -> None:
        """Mutate ``creation.json`` ``sync_status`` to ``synced``. Backend Spec §7.1.4."""
        creation_path = creation_json_path(run_path)
        if not creation_path.exists():
            return

        def _flip(payload: CreationJson) -> CreationJson:
            payload.sync_status = SyncStatus.SYNCED
            return payload

        with contextlib.suppress(Exception):
            await self._cache_creation.update_creation_atomic(creation_path, _flip)

    async def _mark_cleaned(self, run_path: Path) -> None:
        """Mutate ``creation.json`` ``sync_status`` to ``cleaned``. Backend Spec §7.1.10.

        No-op when ``creation.json`` no longer exists (the
        ``retain_cache=False`` path removes the cache directory along with
        the data files).
        """
        creation_path = creation_json_path(run_path)
        if not creation_path.exists():
            return

        def _flip(payload: CreationJson) -> CreationJson:
            payload.sync_status = SyncStatus.CLEANED
            return payload

        with contextlib.suppress(Exception):
            await self._cache_creation.update_creation_atomic(creation_path, _flip)
