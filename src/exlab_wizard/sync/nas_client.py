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
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from exlab_wizard.api.schemas import CreationJson
from exlab_wizard.cache.creation_writer import CreationWriter
from exlab_wizard.cache.sync_state_writer import SyncStateWriter
from exlab_wizard.config.models import (
    Config,
    EquipmentConfig,
    RclonePerf,
)
from exlab_wizard.constants import (
    RunSyncState,
    SyncHandleState,
    SyncMode,
    SyncStatus,
)
from exlab_wizard.logging import get_logger
from exlab_wizard.paths import cache_dir, creation_json_path
from exlab_wizard.sync.bandwidth import effective_bandwidth_limit_kibps
from exlab_wizard.sync.cleanup import cleanup_interlocks_satisfied
from exlab_wizard.sync.manifest import RemoteManifest, parse_lsjson
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
from exlab_wizard.sync.transports.rclone import RcloneDriver
from exlab_wizard.sync.verifier import VerifyResult
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


def _remote_subpath(base_root: str, equipment_id: str, run: Path) -> str:
    """Compose the run-relative ``<base_root>/<equipment_id>/<run-leaf>`` path.

    Leaf semantics preserved: the run directory name is appended after the
    equipment folder. Empty components (a blank ``base_root``) are dropped so
    the composed path never carries a doubled slash. This is both the path
    portion of the rclone target and the ``strip_prefix`` the lsjson reconcile
    removes to recover run-relative keys.
    """
    parts = [base_root.strip("/"), equipment_id, run.name]
    return "/".join(p for p in parts if p)


def _build_driver(config_path: str, perf: RclonePerf) -> RcloneDriver:
    """Construct a :class:`RcloneDriver` for a named remote.

    The named remote lives in the operator's ``rclone.conf`` (set up with
    ``rclone config``); the driver only needs the optional ``--config``
    path override plus the parallelism dials. No keyring / env threading —
    credentials are entirely the named remote's concern.

    Both the NAS leg and the orchestrator stage hop share the same
    ``rclone.conf`` (their remotes live side by side), so the config path
    is always ``nas.rclone_config_path``; only the perf dial differs by
    ``sync_mode`` (the ``nas:`` block's ``perf`` vs
    ``orchestrator.staging_perf``).
    """
    return RcloneDriver(
        config_path=config_path or None,
        transfers=perf.transfers,
        checkers=perf.checkers,
    )


@dataclass(frozen=True, slots=True)
class _RemoteResolution:
    """The ``(remote, base_root, perf)`` an equipment's ops resolve to.

    Computed once per equipment by :meth:`NASSyncClient._resolve_remote`,
    branched by ``sync_mode``: stage-mode equipment use the orchestrator's
    staging remote; every other equipment uses the ``nas:`` block. The
    target string, the lsjson ``strip_prefix``, and the driver's perf dials
    all derive from this one selection so the mode branch lives in exactly
    one place.
    """

    remote: str
    base_root: str
    perf: RclonePerf


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
        keyring_store: Any = None,
        worker_poll_interval_s: float = 0.05,
        push_callable_factory: Callable[[EquipmentConfig], Callable[..., Any]] | None = None,
        check_callable_factory: Callable[[EquipmentConfig], Callable[..., Any]] | None = None,
        lsjson_callable_factory: Callable[[EquipmentConfig], Callable[..., Any]] | None = None,
        remote_stat_callable: Callable[[SyncJobRow], bool] | None = None,
    ) -> None:
        self._config = config
        self._queue_db = queue_db
        self._validator = validator
        self._cache_creation = cache_creation
        self._sync_state_writer = sync_state_writer or SyncStateWriter()
        # Retained for additive compatibility: the rclone-named-remote
        # migration moved credential handling entirely into the operator's
        # ``rclone.conf``, so the client no longer resolves passwords from
        # the keyring. ``tray/dependencies.py`` still passes this arg; it is
        # accepted and ignored here until that caller is migrated.
        self._keyring_store = keyring_store
        self._queue = SyncQueue(queue_db)
        self._equipment_by_id = {e.id: e for e in config.equipment}
        self._worker_poll_interval_s = worker_poll_interval_s
        self._worker_task: asyncio.Task[None] | None = None
        self._wake_event = asyncio.Event()
        self._stopping = False
        self._push_callable_factory = push_callable_factory
        self._check_callable_factory = check_callable_factory
        self._lsjson_callable_factory = lsjson_callable_factory
        # Default remote stat: optimistic OK so unit tests don't need
        # to wire a real network probe.
        self._remote_stat_callable = remote_stat_callable or (lambda _row: True)

    def apply_config(self, config: Config) -> None:
        """Swap the cached config + equipment map in place (no relaunch).

        The ``equipment_id -> EquipmentConfig`` lookup is rebuilt into a
        local before assignment so the worker loop -- which runs in the
        same event loop -- never observes a half-built map. In-flight
        queued jobs carry their own captured paths; a removed equipment id
        simply errors that one job exactly as it would after a relaunch.
        """
        equipment_by_id = {e.id: e for e in config.equipment}
        self._config = config
        self._equipment_by_id = equipment_by_id

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
        """Re-run ``rclone check --download`` against the configured remote.

        Used by the Settings "verify integrity" action. Reports only --
        does NOT advance the queue state and does NOT update
        ``verified_sha256`` in ``sync_state.json`` (the rclone-only
        migration deliberately keeps Slot A SHA capture scoped to the
        sync-time path that has access to a freshly-read local copy).

        Resolves the equipment from ``run_path``'s first component, gathers
        every tracked file in ``sync_state.json`` as the ``--files-from``
        subset, and asks the driver to compare. Returns a populated
        :class:`VerifyResult`; the caller renders ``mismatched``,
        ``missing``, and ``errors`` to the operator. A run with no
        tracked files yields ``ok=True`` (nothing to verify).
        """
        equipment: EquipmentConfig | None = None
        for part in run_path.parts:
            candidate = self._equipment_by_id.get(part)
            if candidate is not None:
                equipment = candidate
                break
        if equipment is None:
            return VerifyResult(
                ok=False,
                error_kind=TransportErrorKind.UNKNOWN,
            )
        state = await self._sync_state_writer.read(run_path)
        files = tuple(sorted(state.files.keys()))
        if not files:
            return VerifyResult(ok=True)
        check = self._build_check(equipment)
        files_from = self._write_files_from(files)
        try:
            try:
                check_result = await check(run_path, files_from=files_from)
            except TransportError as exc:
                return VerifyResult(ok=False, error_kind=exc.error_kind)
        finally:
            with contextlib.suppress(OSError):
                files_from.unlink()
        return VerifyResult.from_check_result(check_result)

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
          ``rclone check --download --combined`` via the check callable.
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
        # nas-mode equipment reach the NAS sync queue. The bandwidth policy
        # is defined once on the ``nas:`` block (rclone.conf NAS-sync
        # migration) rather than per-equipment.
        bwlimit = effective_bandwidth_limit_kibps(
            self._config.nas.bandwidth, now_local=datetime.now()
        )

        # Slot A SHA capture (rclone-only migration, 2026-05-26). Compute
        # the local SHA for every file the job wants to verify (the
        # ``--files-from`` subset for a per-file enqueue, or the whole-
        # run subtree when ``job.files`` is empty -- a whole-run enqueue,
        # the manual force-sync path, or a first-sync poll sweep). Local
        # disk I/O only, no wire cost; files removed mid-pass are simply
        # absent from the resulting dict and the reconcile path skips
        # writing ``verified_sha256`` for them.
        verify_files: tuple[str, ...] = job.files or self._discover_run_files(run_path)
        local_shas = await self._compute_local_shas(run_path, verify_files)

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

            if not result.ok:
                await self._handle_push_failure(job, result)
                return
        finally:
            if files_from_path is not None:
                with contextlib.suppress(OSError):
                    files_from_path.unlink()

        # Push succeeded. Transition RUNNING -> AWAITING_VERIFY.
        await self._queue.transition(job.id, SyncJobState.AWAITING_VERIFY)

        # Routine reconcile (rclone-named-remote migration, 2026-05-28):
        # replace the per-push ``rclone check --download`` hash-verify with a
        # cheap ``rclone lsjson`` listing. A file counts as synced when the
        # remote entry exists, its size equals local, and its modtime is
        # within ``mtime_tolerance_s`` of local. The expensive
        # download-and-rehash gate now runs once, immediately before local
        # deletion, in ``_maybe_cleanup``.
        #
        # An empty subset (a completely empty run dir) short-circuits to
        # VERIFIED on the push alone -- there is nothing to reconcile.
        if not verify_files:
            await self._queue.transition(
                job.id,
                SyncJobState.VERIFIED,
                increment_verify_passes=True,
                verified_at=utc_now_iso(),
            )
            await self._mark_synced(run_path)
            await self._maybe_cleanup(job.id, run_path)
            return

        lsjson = self._build_lsjson(equipment)
        try:
            manifest = await lsjson(run_path)
        except TransportError as exc:
            await self._handle_verify_transport_error(job, exc)
            return

        # Per-file reconciliation + Slot A SHA capture. Credit every file
        # whose remote entry matches the local signature, even when some
        # files in the batch are still missing remotely -- a single
        # lagging file must not block the good ones from being recorded.
        tol = self._config.nas.mtime_tolerance_s
        synced_at = utc_now_iso()
        credited: list[str] = []
        for rel in verify_files:
            sig = self._file_signature(run_path / rel)
            if sig is None:
                continue
            size, mtime_ns = sig
            if manifest.matches(rel, size, mtime_ns / 1e9, tolerance_s=tol):
                with contextlib.suppress(Exception):
                    await self._sync_state_writer.upsert_file(
                        run_path,
                        rel,
                        synced_signature=sig,
                        verified_at=synced_at,
                        verified_sha256=local_shas.get(rel),
                    )
                credited.append(rel)

        if len(credited) != len(verify_files):
            # Some files did not reconcile against the remote listing yet.
            # Re-queue immediately (no backoff) so the next sweep re-pushes
            # and re-reconciles the laggards; the credited files stay
            # recorded in ``sync_state.json``.
            await self._queue.transition(
                job.id,
                SyncJobState.QUEUED,
                last_error="remote_reconcile_incomplete",
                next_attempt_at="",
            )
            return

        # Every file reconciled. Promote to VERIFIED and record one verify
        # pass.
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

    async def _handle_verify_transport_error(self, job: SyncJobRow, exc: TransportError) -> None:
        """Route a verify-phase ``TransportError`` per spec §7.1.5.

        Mirrors the push-phase classification so a reconcile listing that
        fails on auth terminates the job, while a transient network /
        unknown failure schedules a backoff retry. Any other case (an
        unclassified probe failure) falls into the HASH_MISMATCH
        single-retry-then-terminal branch.
        """
        kind = exc.error_kind
        if kind is TransportErrorKind.AUTH:
            await self._queue.record_failure(
                job.id, error=TransportErrorKind.AUTH.value, terminal=True
            )
            return
        if kind in (TransportErrorKind.NETWORK, TransportErrorKind.UNKNOWN):
            await self._queue.record_failure(job.id, error=kind.value, terminal=False)
            return
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

    def _resolve_remote(self, equipment: EquipmentConfig) -> _RemoteResolution:
        """Resolve the ``(remote, base_root, perf)`` triple for ``equipment``.

        The single ``sync_mode`` branch (rclone.conf NAS-sync migration,
        Phase 8): stage-mode equipment push to the orchestrator's staging
        remote (``orchestrator.staging_remote`` + ``staging_base_root`` +
        ``staging_perf``); every other equipment uses the ``nas:`` block
        (named remote + base root + perf). The target string, the lsjson
        ``strip_prefix``, and the driver perf dials all derive from this one
        selection, so the mode branch is never duplicated.
        """
        if equipment.sync_mode == SyncMode.STAGE:
            orch = self._config.orchestrator
            return _RemoteResolution(
                remote=orch.staging_remote,
                base_root=orch.staging_base_root,
                perf=orch.staging_perf,
            )
        nas = self._config.nas
        return _RemoteResolution(remote=nas.remote, base_root=nas.base_root, perf=nas.perf)

    def _target_for_equipment(self, equipment: EquipmentConfig, run: Path) -> str:
        """Compose the rclone target ``<remote>:/<base_root>/<id>/<run-leaf>``.

        The ``(remote, base_root)`` selection comes from
        :meth:`_resolve_remote`; the path portion reuses
        :func:`_remote_subpath`.
        """
        resolution = self._resolve_remote(equipment)
        subpath = _remote_subpath(resolution.base_root, equipment.id, run)
        return f"{resolution.remote}:/{subpath}"

    def _driver_for_equipment(self, equipment: EquipmentConfig) -> RcloneDriver:
        """Build the :class:`RcloneDriver` for ``equipment``.

        The perf dials come from :meth:`_resolve_remote` (``staging_perf`` for
        stage-mode, the ``nas:`` block's ``perf`` otherwise). Every equipment
        shares ``nas.rclone_config_path`` as the ``--config`` override -- the
        staging and NAS remotes live in the same ``rclone.conf``.
        """
        return _build_driver(
            self._config.nas.rclone_config_path, self._resolve_remote(equipment).perf
        )

    def _build_push(self, equipment: EquipmentConfig) -> Callable[..., Any]:
        """Resolve the push callable for ``equipment``.

        Tests can inject a custom factory via the constructor's
        ``push_callable_factory`` argument so they don't need a real
        rclone binary on PATH. The default closure builds the per-equipment
        target + driver (nas remote, or the orchestrator staging remote for
        stage-mode) and calls the named-remote driver.
        """
        if self._push_callable_factory is not None:
            return self._push_callable_factory(equipment)
        driver = self._driver_for_equipment(equipment)

        async def _push(
            local: Path,
            *,
            bwlimit_kibps: int | None,
            files_from: Path | None = None,
        ) -> TransportResult:
            target = self._target_for_equipment(equipment, local)
            return await driver.push(
                local, target, bwlimit_kibps=bwlimit_kibps, files_from=files_from
            )

        return _push

    def _build_check(self, equipment: EquipmentConfig) -> Callable[..., Any]:
        """Resolve the ``rclone check --download`` callable for ``equipment``.

        Tests can inject a custom factory via the constructor's
        ``check_callable_factory`` argument. The default closure builds
        the per-equipment target + driver and calls the named-remote
        driver — the expensive hash-verify is now reserved for the
        pre-deletion integrity gate in :meth:`_maybe_cleanup`.
        """
        if self._check_callable_factory is not None:
            return self._check_callable_factory(equipment)
        driver = self._driver_for_equipment(equipment)

        async def _check(local: Path, *, files_from: Path) -> Any:
            target = self._target_for_equipment(equipment, local)
            return await driver.check(local, target, files_from=files_from)

        return _check

    def _build_lsjson(self, equipment: EquipmentConfig) -> Callable[..., Any]:
        """Resolve the cheap ``rclone lsjson`` reconcile probe for ``equipment``.

        Tests can inject a custom factory via the constructor's
        ``lsjson_callable_factory`` argument so they can supply a stub
        returning a crafted :class:`RemoteManifest`. The default closure
        lists the remote run subtree and parses it into a run-relative
        manifest (the routine post-push reconcile and the cleanup
        existence probe both consume the manifest, never the raw JSON).
        The ``strip_prefix`` uses the per-equipment base root (the staging
        base root for stage-mode).
        """
        if self._lsjson_callable_factory is not None:
            return self._lsjson_callable_factory(equipment)
        driver = self._driver_for_equipment(equipment)
        base_root = self._resolve_remote(equipment).base_root

        async def _lsjson(run: Path) -> RemoteManifest:
            target = self._target_for_equipment(equipment, run)
            raw = await driver.lsjson(target)
            prefix = _remote_subpath(base_root, equipment.id, run)
            return parse_lsjson(raw, strip_prefix=prefix)

        return _lsjson

    async def _compute_local_shas(
        self,
        run_path: Path,
        files: tuple[str, ...],
    ) -> dict[str, str]:
        """Compute the SHA-256 hex digest of each file in ``files``.

        Slot A of the 2026-05-26 rclone-only migration. The dict the
        method returns is keyed by run-relative POSIX path and consumed
        by the routine reconcile in :meth:`_drive_job` to populate
        ``sync_state.json:files[*].verified_sha256``. Files that
        disappear between Slot A and the lsjson reconcile (an equipment
        machine pulled mid-sweep) are simply absent from the dict and
        the reconcile path skips writing ``verified_sha256`` for them.

        Each per-file hash runs in ``asyncio.to_thread`` so a multi-GB
        file does not block the event loop.
        """
        import hashlib

        def _read_and_hash(path: Path) -> str | None:
            try:
                handle = path.open("rb")
            except OSError:
                return None
            try:
                digest = hashlib.sha256()
                while True:
                    chunk = handle.read(65536)
                    if not chunk:
                        break
                    digest.update(chunk)
                return digest.hexdigest()
            finally:
                handle.close()

        out: dict[str, str] = {}
        for rel in files:
            digest = await asyncio.to_thread(_read_and_hash, run_path / rel)
            if digest is not None:
                out[rel] = digest
        return out

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

    @staticmethod
    def _discover_run_files(run_path: Path) -> tuple[str, ...]:
        """Return every non-cache regular file under ``run_path`` as POSIX rel paths.

        Used by ``_drive_job`` whenever ``job.files`` is empty (a whole-
        run enqueue / force-sync / first poll sweep) so the verify pass
        has a concrete subset to scope itself to. Mirrors the
        pre-migration ``compute_local_manifest`` walk in scope -- the
        ``.exlab-wizard/`` cache dir is excluded so we never try to
        verify our own metadata against the NAS.
        """
        from exlab_wizard.constants import CACHE_DIR_NAME

        if not run_path.exists() or not run_path.is_dir():
            return ()
        out: list[str] = []
        for path in sorted(run_path.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(run_path)
            if CACHE_DIR_NAME in rel.parts:
                continue
            out.append(rel.as_posix())
        return tuple(out)

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

        # Integrity gate (rclone-named-remote migration, 2026-05-28): the
        # routine sync path only reconciled size + modtime via lsjson, so
        # the expensive download-and-rehash runs exactly once here, right
        # before any irreversible local deletion. Two stages over the
        # tracked files: (1) a cheap lsjson existence probe confirms every
        # tracked file is present remotely; (2) ``rclone check --download``
        # streams each file back and hashes it locally. Any failure (a
        # transport error, a missing file, or a hash mismatch) defers the
        # run in CLEANUP_ELIGIBLE rather than deleting -- a later sweep
        # retries the gate.
        equipment = self._equipment_by_id.get(job.equipment_id)
        tracked = tuple(sorted(sync_state.files.keys()))
        if equipment is not None and tracked:
            lsjson = self._build_lsjson(equipment)
            try:
                manifest = await lsjson(run_path)
            except TransportError:
                await self._queue.transition(job_id, SyncJobState.CLEANUP_ELIGIBLE)
                return
            if not all(manifest.has(rel) for rel in tracked):
                await self._queue.transition(job_id, SyncJobState.CLEANUP_ELIGIBLE)
                return

            check = self._build_check(equipment)
            files_from = self._write_files_from(tracked)
            try:
                try:
                    check_result = await check(run_path, files_from=files_from)
                except TransportError:
                    await self._queue.transition(job_id, SyncJobState.CLEANUP_ELIGIBLE)
                    return
                verify = VerifyResult.from_check_result(check_result)
            finally:
                with contextlib.suppress(OSError):
                    files_from.unlink()
            if not verify.ok:
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
