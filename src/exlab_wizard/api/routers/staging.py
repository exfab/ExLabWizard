"""``/staging`` router. Backend Spec §13.7, §13.8.

Four endpoints back the orchestrator's staging panel:

* ``GET /staging`` -- enumerate every staged run with its lifecycle
  state, file count, byte total, and elapsed time since last activity.
* ``POST /staging/{run_path}/force-sync`` -- enqueue an immediate
  NAS sync for a specific run (used when the operator wants to skip
  the watcher's polling latency).
* ``POST /staging/{run_path}/clear`` -- delete the local staging copy
  of a sync-verified run (the manual-mode action from §13.7).
* ``POST /staging/clear-verified`` -- bulk-clear every sync-verified
  staged run (Redesign §4.6 footer action).

All four return ``503`` with ``{"code": "internal_error"}`` when no
``Config`` is wired on the app (Redesign §3.1 made the orchestrator
pipeline unconditional, so the legacy ``orchestrator.enabled`` toggle
is gone).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict

from exlab_wizard.api._dependencies import require_deps
from exlab_wizard.config.models import Config
from exlab_wizard.constants import RunSyncState, SyncHandleState
from exlab_wizard.logging import get_logger
from exlab_wizard.orchestrator.staging_clear import clear_run_dir
from exlab_wizard.orchestrator.staging_query import (
    StagedRunSummary,
    list_staged_runs,
)
from exlab_wizard.utils.time import utc_now

__all__ = [
    "ClearResponse",
    "ClearVerifiedResponse",
    "ForceSyncResponse",
    "KeepLocalRequest",
    "KeepLocalResponse",
    "StagedRunRow",
    "StagingListResponse",
    "build_staging_router",
]

_log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Pydantic response models
# ---------------------------------------------------------------------------


class StagedRunRow(BaseModel):
    """One staging-panel row. Backend Spec §13.8."""

    model_config = ConfigDict(extra="forbid")

    path: str
    current_state: str
    equipment_id: str
    project_name: str
    run_kind: str
    file_count: int
    byte_total: int
    elapsed_seconds_since_last_activity: int
    last_activity_at: str


class StagingListResponse(BaseModel):
    """``GET /staging`` response."""

    model_config = ConfigDict(extra="forbid")

    runs: list[StagedRunRow]


class ForceSyncResponse(BaseModel):
    """``POST /staging/{run_path}/force-sync`` response."""

    model_config = ConfigDict(extra="forbid")

    run_path: str
    state: str  # "queued" or "blocked"
    job_id: str | None = None


class ClearResponse(BaseModel):
    """``POST /staging/{run_path}/clear`` response."""

    model_config = ConfigDict(extra="forbid")

    run_path: str
    files_freed: int
    bytes_freed: int


class ClearVerifiedResponse(BaseModel):
    """``POST /staging/clear-verified`` response (Redesign §4.6)."""

    model_config = ConfigDict(extra="forbid")

    cleared_paths: list[str]


class KeepLocalRequest(BaseModel):
    """``POST /staging/{run_path}/keep-local`` request body.

    Operator-free per-file NAS sync design (2026-05-21): the operator
    toggles a file's ``keep_local`` flag from the file-list context menu.
    ``relative_path`` is the run-relative POSIX path of the file within
    the run directory; ``keep_local`` is the desired flag value.
    """

    model_config = ConfigDict(extra="forbid")

    relative_path: str
    keep_local: bool


class KeepLocalResponse(BaseModel):
    """``POST /staging/{run_path}/keep-local`` response."""

    model_config = ConfigDict(extra="forbid")

    run_path: str
    relative_path: str
    keep_local: bool


# ---------------------------------------------------------------------------
# Router builder
# ---------------------------------------------------------------------------


def build_staging_router() -> APIRouter:
    """Construct the ``/staging`` router. Backend Spec §13.7, §13.8."""
    router = APIRouter(tags=["staging"])

    @router.get("/staging", response_model=StagingListResponse)
    async def get_staging(request: Request) -> StagingListResponse:
        deps = require_deps(request)
        config = _require_config(deps)
        rows = list_staged_runs(
            config=config,
            now_utc=utc_now(),
            sync_state_writer=getattr(deps, "sync_state_writer", None),
        )
        return StagingListResponse(runs=[_row_from_summary(s) for s in rows])

    @router.post(
        "/staging/clear-verified",
        response_model=ClearVerifiedResponse,
    )
    async def post_clear_verified(request: Request) -> ClearVerifiedResponse:
        """Bulk-clear every staged run whose NAS sync is verified.

        Redesign §4.6: the file-explorer footer's "Clear verified runs"
        action. Phase 5 keys "clearable" off the ``sync_state.json``
        ``SYNCED`` rollup -- a run is clearable when every tracked file is
        verified on the NAS and the run has not already been cleared.
        """
        deps = require_deps(request)
        config = _require_config(deps)
        cleared: list[str] = []
        for summary in list_staged_runs(
            config=config,
            sync_state_writer=getattr(deps, "sync_state_writer", None),
        ):
            # Only a fully-SYNCED run is clearable; ``cleared`` runs have
            # no staging copy left and ``syncing`` runs are unproven.
            if summary.current_state != RunSyncState.SYNCED.value:
                continue
            run_path = Path(summary.path)
            try:
                files, _bytes = clear_run_dir(run_path)
            except Exception as exc:
                _log.warning("clear-verified: clear failed for %s: %s", run_path, exc)
                continue
            if files > 0:
                cleared.append(str(run_path))
        _log.info("clear-verified bulk action: cleared=%d", len(cleared))
        return ClearVerifiedResponse(cleared_paths=cleared)

    @router.post(
        "/staging/{run_path:path}/keep-local",
        response_model=KeepLocalResponse,
    )
    async def post_keep_local(
        request: Request,
        run_path: str,
        body: KeepLocalRequest,
    ) -> KeepLocalResponse:
        """Toggle a file's ``keep_local`` flag in the run's ``sync_state.json``.

        Operator-free per-file NAS sync design (2026-05-21): a
        ``keep_local`` file still syncs to the NAS but is excluded from
        cleanup deletion. ``sync_state.json`` has a single writer -- the
        orchestrator's :class:`SyncStateWriter` -- so the GUI never writes
        the file directly; it calls this endpoint instead. Returns 503
        when no ``SyncStateWriter`` is wired on the app instance, and 404
        when ``run_path`` is not a real run inside an allowed root.
        """
        deps = require_deps(request)
        config = _require_config(deps)
        writer = getattr(deps, "sync_state_writer", None)
        if writer is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "sync_state_writer_unavailable",
                    "message": "sync-state writer is not wired on this app instance",
                },
            )
        # Path-containment guard: ``run_path`` comes straight from the URL
        # and ``SyncStateWriter`` *creates* ``<run_path>/.exlab-wizard/
        # sync_state.json``. Reject any path that is not a real run -- it
        # must sit under an allowed root and carry a ``creation.json``
        # cache -- so a hostile path (``%2Fetc``) cannot provoke a write.
        path = Path(run_path)
        if not _is_real_run(path, config):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "session_not_found",
                    "message": f"no run found at {run_path}",
                },
            )
        await writer.set_keep_local(path, body.relative_path, body.keep_local)
        _log.info(
            "keep-local toggled via API: run=%s file=%s value=%s",
            run_path,
            body.relative_path,
            body.keep_local,
        )
        return KeepLocalResponse(
            run_path=run_path,
            relative_path=body.relative_path,
            keep_local=body.keep_local,
        )

    @router.post(
        "/staging/{run_path:path}/force-sync",
        response_model=ForceSyncResponse,
    )
    async def post_force_sync(request: Request, run_path: str) -> ForceSyncResponse:
        deps = require_deps(request)
        _require_config(deps)
        nas_sync = _require_nas_sync(deps)
        path = Path(run_path)
        handle = await nas_sync.enqueue(path)
        # ``handle`` is a SyncJobHandle-like object exposing .state / .job_id.
        state_value = getattr(handle, "state", SyncHandleState.QUEUED)
        job_id_value = getattr(handle, "job_id", None) or None
        _log.info(
            "force-sync requested via API: path=%s state=%s job_id=%s",
            run_path,
            state_value,
            job_id_value,
        )
        return ForceSyncResponse(
            run_path=run_path,
            state=str(state_value),
            job_id=job_id_value,
        )

    @router.post(
        "/staging/{run_path:path}/clear",
        response_model=ClearResponse,
    )
    async def post_clear(request: Request, run_path: str) -> ClearResponse:
        deps = require_deps(request)
        config = _require_config(deps)
        path = Path(run_path)
        # Defensive check: only a fully-SYNCED run may be cleared. Phase 5
        # derives the run rollup from ``sync_state.json``; a ``syncing`` run
        # is unproven and a ``cleared`` run has no staging copy left.
        from exlab_wizard.cache.sync_state_writer import SyncStateWriter

        writer = getattr(deps, "sync_state_writer", None)
        if writer is not None:
            state = SyncStateWriter.rollup_state(writer.read_sync(path)).value
            if state != RunSyncState.SYNCED.value:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "staging_not_sync_verified",
                        "message": (
                            f"Cannot clear run in sync state {state!r}; "
                            "only synced runs may be cleared."
                        ),
                    },
                )
        _ = config  # kept for parity / future hooks
        files_freed, bytes_freed = clear_run_dir(path)
        return ClearResponse(
            run_path=run_path,
            files_freed=files_freed,
            bytes_freed=bytes_freed,
        )

    return router


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_from_summary(summary: StagedRunSummary) -> StagedRunRow:
    return StagedRunRow(
        path=summary.path,
        current_state=summary.current_state,
        equipment_id=summary.equipment_id,
        project_name=summary.project_name,
        run_kind=summary.run_kind,
        file_count=summary.file_count,
        byte_total=summary.byte_total,
        elapsed_seconds_since_last_activity=summary.elapsed_seconds_since_last_activity,
        last_activity_at=summary.last_activity_at,
    )


def _is_real_run(run_path: Path, config: Config) -> bool:
    """Return True when ``run_path`` is a real run inside an allowed root.

    A "real run" is a directory that (a) sits under one of the configured
    roots (local_root / staging_root / templates / plugins) -- reusing the
    same containment helper the ``GET /folder`` endpoint uses -- and (b)
    carries a ``.exlab-wizard/creation.json`` cache. The containment check
    guards against a hostile URL path; the ``creation.json`` check ensures
    ``SyncStateWriter`` only ever creates ``sync_state.json`` under a
    genuine run directory.
    """
    from exlab_wizard.api.routers.browse import _path_is_under_allowed_root
    from exlab_wizard.paths import creation_json_path

    try:
        resolved = run_path.resolve()
    except OSError:
        return False
    if not _path_is_under_allowed_root(resolved, config):
        return False
    return creation_json_path(run_path).exists()


def _require_config(deps: Any) -> Config:
    """Return the live :class:`Config` or raise 503 when no config is wired.

    Redesign §3.1: the orchestrator pipeline is always active, so the
    staging endpoints only need to check that a config is present.
    """
    config = getattr(deps, "config", None)
    if config is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "internal_error",
                "message": "config is not wired on this app instance",
            },
        )
    return config


def _require_nas_sync(deps: Any) -> Any:
    nas_sync = getattr(deps, "nas_sync", None)
    if nas_sync is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "internal_error",
                "message": "NAS sync client is not wired on this app instance",
            },
        )
    return nas_sync
