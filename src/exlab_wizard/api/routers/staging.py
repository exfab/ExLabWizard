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
from exlab_wizard.cache.ingest_writer import IngestWriter
from exlab_wizard.config.models import Config
from exlab_wizard.constants import IngestState, SyncHandleState
from exlab_wizard.logging import get_logger
from exlab_wizard.orchestrator.staging_query import (
    StagedRunSummary,
    list_staged_runs,
)
from exlab_wizard.paths import ingest_json_path
from exlab_wizard.utils.time import utc_now

__all__ = [
    "ClearResponse",
    "ClearVerifiedResponse",
    "ForceSyncResponse",
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
        rows = list_staged_runs(config=config, now_utc=utc_now())
        return StagingListResponse(runs=[_row_from_summary(s) for s in rows])

    @router.post(
        "/staging/clear-verified",
        response_model=ClearVerifiedResponse,
    )
    async def post_clear_verified(request: Request) -> ClearVerifiedResponse:
        """Bulk-clear every staged run in ``sync_verified`` state.

        Redesign §4.6: the file-explorer footer's "Clear verified runs"
        action. Routes through the same
        :func:`exlab_wizard.orchestrator.cleanup.clear_run` primitive
        as the per-run endpoint, so failure modes (missing dirs, ingest
        write errors) behave identically. Returns the list of cleared
        run paths so the UI can report a count.
        """
        deps = require_deps(request)
        config = _require_config(deps)
        ingest_writer = _require_ingest_writer(deps)
        # Deferred import: see the per-run /clear endpoint below for the
        # cycle-avoidance rationale.
        from exlab_wizard.orchestrator.cleanup import clear_all_verified

        cleared = await clear_all_verified(
            config=config,
            ingest_writer=ingest_writer,
        )
        _log.info("clear-verified bulk action: cleared=%d", len(cleared))
        return ClearVerifiedResponse(cleared_paths=cleared)

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
        ingest_writer = _require_ingest_writer(deps)
        path = Path(run_path)
        # Defensive check: the spec only allows clearing sync-verified
        # runs (manual mode). The watcher would never call this on
        # earlier states, but the API is operator-facing so we enforce
        # the rule here too.
        ingest_path = ingest_json_path(path)
        if ingest_path.exists():
            try:
                payload = await ingest_writer.read_ingest(ingest_path)
            except Exception:
                payload = None
            if payload is not None and payload.current_state != IngestState.SYNC_VERIFIED:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "staging_not_sync_verified",
                        "message": (
                            f"Cannot clear run in state {payload.current_state!r}; "
                            "only sync_verified runs may be cleared."
                        ),
                    },
                )
        # Deferred import: ``orchestrator.cleanup`` pulls in
        # ``api.schemas`` -> ``api`` package, so a module-level import
        # here creates an ``api.routers.staging`` <-> ``orchestrator``
        # cycle whenever ``orchestrator`` is imported before ``api``.
        from exlab_wizard.orchestrator.cleanup import clear_run

        files_freed, bytes_freed = await clear_run(
            path,
            config=config,
            ingest_writer=ingest_writer,
        )
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


def _require_ingest_writer(deps: Any) -> IngestWriter:
    writer = getattr(deps, "ingest_writer", None)
    if writer is None:
        # Fall back to a freshly constructed writer; the IngestWriter is
        # stateless across calls (one FileLock per ingest path).
        return IngestWriter()
    return writer
