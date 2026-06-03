"""Operator-free, per-file quiescence-driven NAS sync poller.

Operator-free per-file NAS sync design (2026-05-21). The
:class:`QuiescenceSyncPoller` is the single auto-sync trigger for **every**
run pending NAS sync -- orchestrator-staged runs *and* runs acquired
directly on ``nas``-mode equipment. It supersedes the sentinel/manifest
``StagingWatcher`` and its five-state ``ingest.json`` machine.

Each sweep (``poll_once``):

1. **Discover runs** in both roots:
   * stage-mode -- run-leaf directories under
     ``config.orchestrator.staging_root``;
   * nas-mode -- run-leaf directories under each ``nas``-mode equipment's
     ``local_root`` tree (``<local_root>/<equipment_id>/<project>/{Runs,
     TestRuns}/<Run_*>``).
2. **Per-file quiescence.** The poller keeps an in-memory snapshot across
   sweeps: for each file, its ``(st_size, st_mtime_ns)`` signature and the
   wall-clock time the signature was *first observed*. A file is **quiet**
   once that signature has been observed unchanged for at least
   ``config.sync.quiescence_minutes``. Files matching any
   ``config.sync.ignore_globs`` glob and the ``.exlab-wizard/`` cache dir
   are skipped. Eligibility is measured from the poller's own observations
   across sweeps -- *not* the absolute age of ``mtime`` -- because
   transports (``rsync -t``, ``rclone``) preserve the source ``mtime``.
3. **Per-file eligibility.** A quiet file is *eligible* when its current
   ``(st_size, st_mtime_ns)`` signature differs from the
   ``synced_signature`` recorded for it in the run's ``sync_state.json``
   (a file with no record, or a record carrying a stale signature, is
   eligible; a file matching its recorded signature has already synced at
   its current state and is skipped).
4. **Enqueue.** Each discovered run with at least one eligible file is
   enqueued via ``nas_sync.enqueue(run_path, files=[...])`` carrying the
   run-relative paths of the eligible files. ``enqueue`` itself owns the
   re-queue / no-op decision (a terminal job with a fresh subset is
   re-armed; an active job is a no-op), so the poller no longer needs the
   coarse "skip run with a job" guard.

The poller is safe to cancel at any await point: it carries no on-disk
state of its own (the snapshot is purely in-memory and is rebuilt by
re-observing the filesystem on the next sweep).
"""

from __future__ import annotations

import asyncio
import contextlib
import fnmatch
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from exlab_wizard.config.models import Config
from exlab_wizard.constants import CACHE_DIR_NAME, SyncMode
from exlab_wizard.logging import get_logger
from exlab_wizard.orchestrator._scan import walk_equipment_run_leaves, walk_run_leaves

if TYPE_CHECKING:
    from exlab_wizard.cache.sync_state_writer import SyncStateWriter

__all__ = ["FileSnapshot", "NASSyncLike", "QuiescenceSyncPoller"]

_log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Protocols (kept loose so production NASSyncClient and test stubs both fit)
# ---------------------------------------------------------------------------


class NASSyncLike(Protocol):
    """The NAS-sync surface the poller and the Phase 3 staging code use.

    ``enqueue`` is what the poller itself calls; the ``staging`` router and
    ``ui/mount`` run-status code additionally consult ``status`` /
    ``get_by_run_path`` / ``list_all``. They are declared here so the
    protocol documents the full surface that ``deps.nas_sync`` must
    satisfy. Tests pass in-memory stubs that record the calls.
    """

    async def enqueue(self, run_path: Path, files: list[str] | None = ...) -> Any: ...
    async def status(self, run_path: Path) -> str: ...
    async def get_by_run_path(self, run_path: Path) -> Any: ...
    async def list_all(self) -> Any: ...


@dataclass(slots=True)
class FileSnapshot:
    """One file's observation record carried forward across sweeps.

    * ``signature`` -- the ``(st_size, st_mtime_ns)`` last observed.
    * ``first_seen_monotonic`` -- the ``time.monotonic()`` value at which
      that exact signature was *first* observed. Reset whenever the
      signature changes; the settle window is measured from it.
    """

    signature: tuple[int, int]
    first_seen_monotonic: float


class QuiescenceSyncPoller:
    """Polls every run pending NAS sync and enqueues runs with eligible files.

    Constructor dependencies are a :class:`Config`, a
    :class:`NASSyncClient`-shaped sync client, and a :class:`SyncStateWriter`
    used (read-only) to skip files already synced at their current
    ``(st_size, st_mtime_ns)`` signature.

    The start/stop/loop lifecycle mirrors the retired ``StagingWatcher``;
    ``poll_once`` is exposed so tests can drive the poller synchronously.
    """

    def __init__(
        self,
        *,
        config: Config,
        nas_sync: NASSyncLike,
        sync_state_writer: SyncStateWriter,
    ) -> None:
        self._config = config
        self._nas_sync = nas_sync
        self._sync_state_writer = sync_state_writer
        self._task: asyncio.Task[None] | None = None
        self._stopping = False
        # Per-file observation snapshot, keyed by absolute path, carried
        # across sweeps so the settle window can be measured.
        self._snapshots: dict[Path, FileSnapshot] = {}

    def apply_config(self, config: Config) -> None:
        """Swap the cached config in place so a live settings save applies.

        ``poll_once`` re-reads ``quiescence_minutes``, the equipment list,
        the staging root, and the ignore globs from ``self._config`` every
        sweep, and :meth:`_loop` re-reads ``poll_interval_seconds`` each
        iteration -- so reassigning the config here makes every sync
        setting take effect on the next sweep without a tray relaunch.
        """
        self._config = config

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        """Start the background polling task. Idempotent.

        Returns immediately; the task runs until :meth:`stop` is called or
        the surrounding event loop tears down.
        """
        if self._task is not None and not self._task.done():
            return
        self._stopping = False
        self._task = asyncio.create_task(self._loop(), name="exlab-quiescence-poller")
        _log.info(
            "quiescence poller started: poll_interval_s=%d quiescence_minutes=%d",
            self._config.sync.poll_interval_seconds,
            self._config.sync.quiescence_minutes,
        )

    async def stop(self) -> None:
        """Cancel the background task and wait for it to exit. Idempotent."""
        self._stopping = True
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await self._task
        self._task = None
        _log.info("quiescence poller stopped")

    # ------------------------------------------------------------------ poll loop

    async def _loop(self) -> None:
        try:
            while not self._stopping:
                # Re-read each iteration so a live ``apply_config`` swap of
                # ``poll_interval_seconds`` takes effect on the next sweep
                # without a tray relaunch.
                interval = float(self._config.sync.poll_interval_seconds)
                try:
                    await self.poll_once()
                except asyncio.CancelledError:
                    raise
                except Exception:  # pragma: no cover -- defensive
                    _log.exception("quiescence poller sweep failed")
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(
                        asyncio.shield(asyncio.sleep(interval)),
                        timeout=interval + 1.0,
                    )
        except asyncio.CancelledError:
            raise

    async def poll_once(self, *, now_monotonic: float | None = None) -> list[Path]:
        """Run one discovery + quiescence + enqueue sweep.

        ``now_monotonic`` is injectable so tests can drive the settle
        window with a controllable clock; production callers leave it
        ``None`` and the poller reads ``time.monotonic()``.

        Returns the list of run paths enqueued on this sweep (in discovery
        order) so tests can assert exactly which runs fired.
        """
        now = time.monotonic() if now_monotonic is None else now_monotonic
        runs = self._discover_runs()
        settle_seconds = self._config.sync.quiescence_minutes * 60
        # Build the next-sweep snapshot fresh; files that vanished simply
        # fall out of the carried-forward dict.
        next_snapshots: dict[Path, FileSnapshot] = {}
        enqueued: list[Path] = []
        for run_path in runs:
            quiet_files: list[Path] = []
            for file_path in self._iter_run_files(run_path):
                signature = self._signature(file_path)
                if signature is None:
                    continue
                prior = self._snapshots.get(file_path)
                if prior is not None and prior.signature == signature:
                    snap = FileSnapshot(signature, prior.first_seen_monotonic)
                else:
                    snap = FileSnapshot(signature, now)
                next_snapshots[file_path] = snap
                if now - snap.first_seen_monotonic >= settle_seconds:
                    quiet_files.append(file_path)
            if not quiet_files:
                continue
            # Per-file eligibility: a quiet file is enqueued only when its
            # current signature differs from the ``synced_signature``
            # recorded in ``sync_state.json`` (a file with no record, or a
            # record with a stale signature, is eligible). ``enqueue``
            # itself owns the re-queue / no-op decision against any
            # existing job, so the poller no longer pre-filters on job
            # state.
            eligible = await self._eligible_files(run_path, quiet_files)
            if eligible:
                await self._nas_sync.enqueue(run_path, eligible)
                enqueued.append(run_path)
        self._snapshots = next_snapshots
        return enqueued

    # ------------------------------------------------------------------ discovery

    def _discover_runs(self) -> list[Path]:
        """Return every run-leaf directory pending NAS sync.

        Stage-mode runs sit under ``orchestrator.staging_root`` (an
        equipment-first root). Nas-mode runs sit under each ``nas``-mode
        equipment's *own* subtree ``<local_root>/<equipment_id>`` -- NOT
        the whole ``local_root`` tree, which is shared across equipment
        (a co-rooted ``stage``-mode equipment's runs must not be swept in
        here, they reach the NAS via the orchestrator's staging area). A
        run discovered through both roots is de-duplicated.
        """
        seen: set[Path] = set()
        runs: list[Path] = []

        def _add(leaf: Path) -> None:
            resolved = _safe_resolve(leaf)
            if resolved in seen:
                return
            seen.add(resolved)
            runs.append(leaf)

        staging_root = self._config.orchestrator.staging_root
        if staging_root:
            for leaf in walk_run_leaves(Path(staging_root)):
                _add(leaf)
        data_root = self._config.paths.local_root
        for equipment in self._config.equipment:
            if equipment.sync_mode != SyncMode.NAS:
                continue
            # Runs live at ``<data_root>/<equipment_id>/<project>/...`` where
            # ``data_root`` is the single derived ``<app_root>/data`` -- the
            # same base run creation composes against, so the poller never
            # watches a different tree than runs are written to. Walk only this
            # equipment's own subtree so a co-rooted ``stage``-mode equipment is
            # never discovered here.
            equipment_dir = Path(data_root) / equipment.id
            for leaf in walk_equipment_run_leaves(equipment_dir):
                _add(leaf)
        return runs

    # ------------------------------------------------------------------ quiescence

    def _iter_run_files(self, run_path: Path) -> list[Path]:
        """Return every non-ignored file under ``run_path``.

        Skips the ``.exlab-wizard/`` cache dir and any file whose name
        matches a ``config.sync.ignore_globs`` glob.
        """
        ignore_globs = self._config.sync.ignore_globs
        out: list[Path] = []
        stack: list[Path] = [run_path]
        while stack:
            current = stack.pop()
            try:
                entries = list(os.scandir(current))
            except (FileNotFoundError, NotADirectoryError, PermissionError):
                continue
            for entry in entries:
                if entry.name == CACHE_DIR_NAME and current == run_path:
                    continue
                try:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(Path(entry.path))
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                if _matches_any_glob(entry.name, ignore_globs):
                    continue
                out.append(Path(entry.path))
        return out

    @staticmethod
    def _signature(file_path: Path) -> tuple[int, int] | None:
        """Return ``(st_size, st_mtime_ns)`` for ``file_path`` or None."""
        try:
            stat = file_path.stat()
        except OSError:
            return None
        return (stat.st_size, stat.st_mtime_ns)

    async def _eligible_files(
        self,
        run_path: Path,
        quiet_files: list[Path],
    ) -> list[str]:
        """Return the run-relative paths of quiet files needing a (re-)sync.

        A quiet file is eligible when its current ``(st_size,
        st_mtime_ns)`` signature differs from the ``synced_signature``
        recorded for it in the run's ``sync_state.json`` -- i.e. it has
        never synced, or it was modified after a prior sync. A file whose
        signature matches its recorded ``synced_signature`` has already
        synced at its current state and is skipped.

        Returns paths sorted for determinism. On a ``sync_state.json`` read
        failure the poller treats every quiet file as eligible (fail-open:
        re-syncing an already-synced file is wasteful but safe).
        """
        try:
            state = await self._sync_state_writer.read(run_path)
        except Exception as exc:  # pragma: no cover -- defensive
            _log.warning("sync_state.json read failed for %s: %s", run_path, exc)
            synced: dict[str, tuple[int, int] | None] = {}
        else:
            synced = {rel: rec.synced_signature for rel, rec in state.files.items()}

        eligible: list[str] = []
        for file_path in quiet_files:
            signature = self._signature(file_path)
            if signature is None:
                continue
            rel = file_path.relative_to(run_path).as_posix()
            recorded = synced.get(rel)
            if recorded is not None and tuple(recorded) == signature:
                continue
            eligible.append(rel)
        return sorted(eligible)


def _matches_any_glob(name: str, globs: list[str]) -> bool:
    """Return True if ``name`` matches any glob in ``globs``."""
    return any(fnmatch.fnmatch(name, pattern) for pattern in globs)


def _safe_resolve(path: Path) -> Path:
    """Resolve ``path`` for de-dup, falling back to the path itself."""
    try:
        return path.resolve()
    except OSError:
        return path
