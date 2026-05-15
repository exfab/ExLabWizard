"""Background staging watcher. Backend Spec §12, §13.

The :class:`StagingWatcher` is a polling task that drives the five-state
lifecycle (§13.3) for every directory under ``staging_root``:

    staging -> complete -> sync_queued -> sync_verified -> cleared

For each newly-discovered run:

1. Read the equipment-side ``creation.json`` already pushed into
   ``<run>/.exlab-wizard/creation.json`` and produce the initial
   ``ingest.json`` with ``current_state == staging``.
2. Watch the run for the configured completeness signal (sentinel file
   or manifest comparison; §13.5). Promote ``staging`` -> ``complete``.
3. Enqueue with the supplied :class:`NASSyncClient`. On a successful
   enqueue, promote ``complete`` -> ``sync_queued``.
4. Poll the NAS sync status. On ``verified`` (or its post-cleanup
   states), promote ``sync_queued`` -> ``sync_verified``.
5. If the cleanup policy is ``scheduled`` and ``retain_hours`` has
   elapsed since ``sync_verified``, invoke :func:`clear_run` and
   promote ``sync_verified`` -> ``cleared``. ``manual`` mode never
   auto-clears -- the operator must invoke the action explicitly.

The watcher is designed to be safe to cancel at any await point: state
transitions land on disk in a single locked write per :class:`IngestWriter`,
so a partial run leaves a coherent file. Re-running the loop picks up
from the on-disk state.

The watcher is **not** the place where the run completeness is decided
in the production sense -- the equipment machine pushes a sentinel file
or manifest, and the watcher merely observes its presence. This keeps
transport and acquisition policy out of the app per §13.6.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import msgspec

from exlab_wizard.api.schemas import CreationJson, IngestJson
from exlab_wizard.cache.ingest_writer import IngestWriter, default_host
from exlab_wizard.config.models import Config, EquipmentConfig
from exlab_wizard.constants import (
    INGEST_JSON_VERSION,
    CompletenessSignal,
    IngestState,
    OrchestratorTransportType,
    RunKind,
)
from exlab_wizard.io import read_msgspec_json_raw
from exlab_wizard.logging import get_logger
from exlab_wizard.orchestrator._scan import (
    count_files_and_bytes,
    walk_run_leaves,
)
from exlab_wizard.orchestrator.cleanup import cleanup_eligible, clear_run
from exlab_wizard.paths import (
    creation_json_path,
    ingest_json_path,
    is_run_dir,
    is_test_run_dir,
)
from exlab_wizard.sync.queue import SyncJobState
from exlab_wizard.utils.time import utc_now_iso

__all__ = ["NASSyncLike", "StagingWatcher"]

_log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Protocols (kept loose so production NASSyncClient and test stubs both fit)
# ---------------------------------------------------------------------------


class NASSyncLike(Protocol):
    """The subset of :class:`NASSyncClient` the watcher uses.

    Only ``enqueue(run_path)`` and ``status(run_path)`` are needed.
    Tests pass an in-memory stub that records the calls.
    """

    async def enqueue(self, run_path: Path) -> Any: ...
    async def status(self, run_path: Path) -> str: ...


class CreationCacheLike(Protocol):
    """The subset of :class:`CreationWriter` the watcher uses."""

    async def read_creation_snapshot(self, path: Path) -> CreationJson: ...


# Verified statuses reported by NASSyncClient.status() (see Backend Spec §7.1.2).
# Anything in this set means the NAS copy is durably present. Derived from the
# queue-internal SyncJobState rather than a separate string set so the status
# values stay in sync with the queue's state machine (Backend Spec §7.1.2).
_VERIFIED_STATUSES: frozenset[str] = frozenset(
    {
        SyncJobState.VERIFIED.value,
        SyncJobState.CLEANUP_ELIGIBLE.value,
        SyncJobState.CLEANED.value,
    },
)


@dataclass
class _RunLocator:
    """Computed values for the staged run we are evaluating."""

    run_path: Path
    cache_dir: Path
    creation_path: Path
    ingest_path: Path
    equipment_id: str
    project_name: str
    run_kind: RunKind


class StagingWatcher:
    """Polls ``staging_root`` and drives the §13.3 lifecycle.

    Constructor arguments mirror the spec: a :class:`Config`, the
    orchestrator-side :class:`IngestWriter`, a :class:`NASSyncClient`-shaped
    sync client, the :class:`CreationWriter` used to read pushed creation
    snapshots, and an optional ``on_state_change`` callable invoked after
    every successful transition (used by the UI to refresh the panel).

    Polling cadence defaults to 10s (§13.5 -- "polls until all are
    present") and is overridable for tests via ``poll_interval_s``.
    """

    def __init__(
        self,
        *,
        config: Config,
        ingest_writer: IngestWriter,
        nas_sync: NASSyncLike,
        cache_creation: CreationCacheLike,
        on_state_change: Callable[[Path, IngestState], Awaitable[None] | None] | None = None,
        poll_interval_s: float = 10.0,
    ) -> None:
        self._config = config
        self._ingest = ingest_writer
        self._nas_sync = nas_sync
        self._cache_creation = cache_creation
        self._on_state_change = on_state_change
        self._poll_interval_s = poll_interval_s
        self._task: asyncio.Task[None] | None = None
        self._stopping = False
        self._equipment_by_id: dict[str, EquipmentConfig] = {e.id: e for e in config.equipment}

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        """Start the background polling task. Idempotent.

        Returns immediately; the task runs until :meth:`stop` is called
        or the surrounding event loop tears down.
        """
        if self._task is not None and not self._task.done():
            return
        # Redesign §3.1: orchestrator pipeline is always active. The watcher
        # starts unconditionally; it stays a no-op when the configured
        # staging_root does not exist (handled per-poll in poll_once).
        self._stopping = False
        self._task = asyncio.create_task(self._loop(), name="exlab-staging-watcher")
        _log.info(
            "staging watcher started: staging_root=%s poll_interval_s=%.1f",
            self._config.orchestrator.staging_root,
            self._poll_interval_s,
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
        _log.info("staging watcher stopped")

    # ------------------------------------------------------------------ poll loop

    async def _loop(self) -> None:
        try:
            while not self._stopping:
                try:
                    await self.poll_once()
                except asyncio.CancelledError:
                    raise
                except Exception:  # pragma: no cover -- defensive
                    _log.exception("staging watcher poll failed")
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(
                        asyncio.shield(asyncio.sleep(self._poll_interval_s)),
                        timeout=self._poll_interval_s + 1.0,
                    )
        except asyncio.CancelledError:
            raise

    async def poll_once(self) -> list[IngestState]:
        """Walk staging_root once and run :meth:`evaluate_run` on each leaf.

        Exposed publicly so tests can drive the watcher synchronously
        without spinning up the asyncio task. Returns the list of
        post-evaluation states for every run found (in walk order).
        """
        staging_root = Path(self._config.orchestrator.staging_root)
        if not staging_root.exists():  # noqa: ASYNC240 -- one-shot stat
            return []
        states: list[IngestState] = []
        for run_path in self._walk_run_leaves(staging_root):
            try:
                state = await self.evaluate_run(run_path)
            except Exception as exc:  # pragma: no cover -- defensive
                _log.warning("evaluate_run failed for %s: %s", run_path, exc)
                continue
            states.append(state)
        return states

    # ------------------------------------------------------------------ evaluate_run

    async def evaluate_run(self, run_path: Path) -> IngestState:
        """Evaluate one run and advance its state if conditions are met.

        Returns the post-evaluation state. Idempotent within a single
        cycle -- calling repeatedly while no condition has changed is a
        cheap no-op (only reads the on-disk ingest entry).

        State decisions, in order:

        1. No ``ingest.json`` yet -- bootstrap one in ``staging`` from
           the equipment's pushed ``creation.json``.
        2. ``staging`` -- if the equipment-defined completeness signal is
           present, advance to ``complete`` (recording file/byte counts).
        3. ``complete`` -- enqueue with NASSyncClient; on success
           advance to ``sync_queued``.
        4. ``sync_queued`` -- check NAS sync status; on verified-or-better
           advance to ``sync_verified``.
        5. ``sync_verified`` -- if cleanup policy says we may clear,
           invoke :func:`clear_run` and advance to ``cleared``.
        6. ``cleared`` -- terminal; nothing more to do.
        """
        loc = self._locator_for(run_path)
        if loc is None:
            return IngestState.STAGING  # unrecognised shape; reported as no-op

        # Bootstrap an ingest.json if missing.
        if not loc.ingest_path.exists():
            await self._bootstrap_initial_ingest(loc)
            return IngestState.STAGING

        ingest = await self._ingest.read_ingest(loc.ingest_path)
        current = IngestState(ingest.current_state)
        match current:
            case IngestState.STAGING:
                if await self._completeness_signal_present(loc):
                    files, bytes_received = self._count_files_and_bytes(run_path)
                    await self._advance(
                        loc,
                        next_state=IngestState.COMPLETE,
                        files_received=files,
                        bytes_received=bytes_received,
                    )
                    return IngestState.COMPLETE
                return IngestState.STAGING

            case IngestState.COMPLETE:
                await self._nas_sync.enqueue(run_path)
                await self._advance(loc, next_state=IngestState.SYNC_QUEUED)
                return IngestState.SYNC_QUEUED

            case IngestState.SYNC_QUEUED:
                status = await self._nas_sync.status(run_path)
                if status in _VERIFIED_STATUSES:
                    await self._advance(
                        loc,
                        next_state=IngestState.SYNC_VERIFIED,
                        nas_path=ingest.run_path or "",
                    )
                    return IngestState.SYNC_VERIFIED
                return IngestState.SYNC_QUEUED

            case IngestState.SYNC_VERIFIED:
                if cleanup_eligible(ingest=ingest, config=self._config):
                    await clear_run(
                        run_path,
                        config=self._config,
                        ingest_writer=self._ingest,
                    )
                    await self._notify(run_path, IngestState.CLEARED)
                    return IngestState.CLEARED
                return IngestState.SYNC_VERIFIED

            case IngestState.CLEARED:
                return IngestState.CLEARED

        return current

    # ------------------------------------------------------------------ bootstrap

    async def _bootstrap_initial_ingest(self, loc: _RunLocator) -> None:
        """Write the initial ``ingest.json`` payload (state == staging).

        Uses the equipment-side ``creation.json`` for project / kind /
        equipment id when available; otherwise uses path-derived defaults.
        """
        creation = await self._read_creation_safe(loc.creation_path)
        # The path-derived equipment id is authoritative because the staging
        # tree mirrors the NAS layout (§13.2). The creation snapshot is
        # used only for descriptive metadata (project name, run kind).
        equipment_id = loc.equipment_id
        equipment = self._equipment_by_id.get(equipment_id)
        transport = self._infer_transport(equipment)
        run_kind = creation.run_kind if creation is not None else loc.run_kind
        project_name = self._project_name_from_creation(creation) or loc.project_name
        host = default_host()
        run_relative = self._run_relative_path(loc.run_path)
        payload = IngestJson(
            schema_version=INGEST_JSON_VERSION,
            project_name=project_name,
            equipment_id=equipment_id,
            run_kind=run_kind,
            run_path=run_relative,
            transport=transport,
            current_state=IngestState.STAGING,
            history=[
                {
                    "state": IngestState.STAGING.value,
                    "at": utc_now_iso(),
                    "host": host,
                },
            ],
        )
        await self._ingest.write_ingest(loc.ingest_path, payload)
        await self._notify(loc.run_path, IngestState.STAGING)

    async def _read_creation_safe(self, path: Path) -> CreationJson | None:
        """Read ``creation.json`` if present and parsable, else ``None``."""
        if not path.exists():  # noqa: ASYNC240 -- one-shot stat
            return None
        try:
            return await self._cache_creation.read_creation_snapshot(path)
        except Exception as exc:
            _log.warning("creation.json at %s could not be read: %s", path, exc)
            return None

    @staticmethod
    def _project_name_from_creation(creation: CreationJson | None) -> str | None:
        if creation is None:
            return None
        return creation.lims_project.name_at_creation or creation.lims_project.short_id or None

    def _infer_transport(self, equipment: EquipmentConfig | None) -> OrchestratorTransportType:
        """Return the configured staging transport, or a sensible default."""
        if equipment is None or equipment.orchestrator_staging_transport is None:
            return OrchestratorTransportType.SMB_MOUNT
        return equipment.orchestrator_staging_transport.type

    # ------------------------------------------------------------------ helpers

    async def _advance(
        self,
        loc: _RunLocator,
        *,
        next_state: IngestState,
        files_received: int | None = None,
        bytes_received: int | None = None,
        nas_path: str | None = None,
        checksum_file: str | None = None,
    ) -> None:
        """Append the state transition + invoke ``on_state_change`` hook."""
        await self._ingest.append_state_transition(
            loc.ingest_path,
            next_state,
            host=default_host(),
            files_received=files_received,
            bytes_received=bytes_received,
            nas_path=nas_path,
            checksum_file=checksum_file,
        )
        await self._notify(loc.run_path, next_state)

    async def _notify(self, run_path: Path, state: IngestState) -> None:
        """Invoke the optional ``on_state_change`` hook (sync or async)."""
        if self._on_state_change is None:
            return
        result = self._on_state_change(run_path, state)
        if asyncio.iscoroutine(result):
            await result

    async def _completeness_signal_present(self, loc: _RunLocator) -> bool:
        """Return True if the equipment's configured signal is present.

        The check is per-equipment per §13.5:

        * ``sentinel_file`` -- a file with ``equipment.sentinel_filename``
          exists in the run leaf.
        * ``manifest`` -- a file with ``equipment.manifest_filename``
          exists AND every file it lists is present with the right size.

        Redesign §3.3: if the equipment isn't in this device's local
        registry (received-equipment path), the signal config travels
        with the pushed ``creation.json`` ``orchestrator`` block so the
        watcher can auto-discover what to look for without a per-equipment
        config of its own.
        """
        signal_kind, sentinel_filename, manifest_filename = await self._completeness_signal_for(loc)
        if signal_kind is None:
            return False
        match signal_kind:
            case CompletenessSignal.SENTINEL_FILE:
                if not sentinel_filename:
                    return False
                return (loc.run_path / sentinel_filename).is_file()
            case CompletenessSignal.MANIFEST:
                if not manifest_filename:
                    return False
                return _manifest_satisfied(
                    loc.run_path / manifest_filename,
                    loc.run_path,
                )
        return False

    async def _completeness_signal_for(
        self,
        loc: _RunLocator,
    ) -> tuple[CompletenessSignal | None, str | None, str | None]:
        """Resolve the completeness-signal triple for ``loc``.

        For owned equipment (``loc.equipment_id`` is in the local
        registry), reads from ``EquipmentConfig``. For received equipment
        (Redesign §3.3 auto-discovery), falls back to the
        ``orchestrator`` block of the pushed ``creation.json`` which
        carries the relay-discovery fields. Returns ``(None, None, None)``
        if neither source has the info.
        """
        equipment = self._equipment_by_id.get(loc.equipment_id)
        if equipment is not None:
            return (
                equipment.completeness_signal,
                equipment.sentinel_filename,
                equipment.manifest_filename,
            )
        creation = await self._read_creation_safe(loc.creation_path)
        if creation is None or creation.orchestrator is None:
            return (None, None, None)
        return (
            creation.orchestrator.completeness_signal,
            creation.orchestrator.sentinel_filename,
            creation.orchestrator.manifest_filename,
        )

    # ------------------------------------------------------------------ scanning

    def _walk_run_leaves(self, staging_root: Path) -> list[Path]:
        """Return every ``Run_*`` / ``TestRun_*`` directory under staging_root."""
        return walk_run_leaves(staging_root)

    def _locator_for(self, run_path: Path) -> _RunLocator | None:
        """Compute a :class:`_RunLocator` for the run, or None if path is wrong shape."""
        try:
            staging_root = Path(self._config.orchestrator.staging_root).resolve()
            relative = run_path.resolve().relative_to(staging_root)
        except (ValueError, OSError):
            return None
        parts = relative.parts
        equipment_id = parts[0] if parts else ""
        # The project name sits at parts[1] and the run leaf is parts[-1].
        project_name = parts[1] if len(parts) >= 2 else ""
        run_name = run_path.name
        if is_test_run_dir(run_name):
            run_kind = RunKind.TEST
        elif is_run_dir(run_name):
            run_kind = RunKind.EXPERIMENTAL
        else:
            return None
        creation_path = creation_json_path(run_path)
        return _RunLocator(
            run_path=run_path,
            cache_dir=creation_path.parent,
            creation_path=creation_path,
            ingest_path=ingest_json_path(run_path),
            equipment_id=equipment_id,
            project_name=project_name,
            run_kind=run_kind,
        )

    def _run_relative_path(self, run_path: Path) -> str:
        """Return ``run_path`` relative to the staging root (forward-slash)."""
        try:
            staging_root = Path(self._config.orchestrator.staging_root).resolve()
            return run_path.resolve().relative_to(staging_root).as_posix()
        except (ValueError, OSError):
            return run_path.as_posix()

    @staticmethod
    def _count_files_and_bytes(run_path: Path) -> tuple[int, int]:
        """Count files + bytes under ``run_path`` excluding the cache dir."""
        return count_files_and_bytes(run_path, exclude_cache=True)


def _manifest_satisfied(manifest_path: Path, run_path: Path) -> bool:
    """Return True if ``manifest_path`` exists and every listed file is present.

    Manifest format is the spec-implicit ``{"files": [{"path": ..., "size":
    ...}]}`` shape. Sizes are compared by exact equality. A manifest with
    no ``files`` array is treated as "no files expected" -- which means
    the run is complete the moment the manifest itself is on disk.
    """
    if not manifest_path.is_file():
        return False
    try:
        data = read_msgspec_json_raw(manifest_path)
    except (msgspec.DecodeError, OSError):
        return False
    if not isinstance(data, dict):
        return False
    files = data.get("files", [])
    if not isinstance(files, list):
        return False
    for entry in files:
        if not isinstance(entry, dict):
            return False
        relative = entry.get("path")
        size = entry.get("size")
        if not isinstance(relative, str) or not relative:
            return False
        target = run_path / relative
        if not target.is_file():
            return False
        if isinstance(size, int):
            try:
                if target.stat().st_size != size:
                    return False
            except OSError:
                return False
    return True
