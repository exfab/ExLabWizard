"""``/staging`` router. Backend Spec §13.7, §13.8.

Three endpoints back the orchestrator's staging panel:

* ``GET /staging`` -- enumerate every staged run with its lifecycle
  state, file count, byte total, and elapsed time since last activity.
* ``POST /staging/{run_path}/force-sync`` -- enqueue an immediate
  NAS sync for a specific run (used when the operator wants to skip
  the watcher's polling latency).
* ``POST /staging/{run_path}/clear`` -- delete the local staging copy
  of a sync-verified run (the manual-mode action from §13.7).

All three return ``503`` with ``{"code": "orchestrator_disabled"}``
when ``config.orchestrator.enabled`` is False -- the router is only
mounted when the flag is True (see :func:`api.app.create_app`), but the
guard is kept here so a misconfigured deployment surfaces a clear error.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict

from exlab_wizard.cache.ingest_writer import IngestWriter
from exlab_wizard.config.models import Config
from exlab_wizard.constants import IngestState
from exlab_wizard.logging import get_logger
from exlab_wizard.orchestrator.cleanup import clear_run
from exlab_wizard.orchestrator.staging_query import (
    StagedRunSummary,
    list_staged_runs,
)
from exlab_wizard.paths import ingest_json_path

__all__ = [
    "ClearResponse",
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


# ---------------------------------------------------------------------------
# Router builder
# ---------------------------------------------------------------------------


def build_staging_router() -> APIRouter:
    """Construct the ``/staging`` router. Backend Spec §13.7, §13.8."""
    router = APIRouter(tags=["staging"])

    @router.get("/staging", response_model=StagingListResponse)
    async def get_staging(request: Request) -> StagingListResponse:
        deps = _require_deps(request)
        config = _require_orchestrator_enabled(deps)
        rows = list_staged_runs(config=config, now_utc=datetime.now(tz=UTC))
        return StagingListResponse(runs=[_row_from_summary(s) for s in rows])

    @router.post(
        "/staging/{run_path:path}/force-sync",
        response_model=ForceSyncResponse,
    )
    async def post_force_sync(request: Request, run_path: str) -> ForceSyncResponse:
        deps = _require_deps(request)
        _require_orchestrator_enabled(deps)
        nas_sync = _require_nas_sync(deps)
        path = Path(run_path)
        handle = await nas_sync.enqueue(path)
        # ``handle`` is a SyncJobHandle-like object exposing .state / .job_id.
        state_value = getattr(handle, "state", "queued")
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
        deps = _require_deps(request)
        config = _require_orchestrator_enabled(deps)
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
            if payload is not None and payload.current_state != IngestState.SYNC_VERIFIED.value:
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


def _require_deps(request: Request) -> Any:
    deps = getattr(request.app.state, "dependencies", None)
    if deps is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "internal_error",
                "message": "app dependencies are not initialized",
            },
        )
    return deps


def _require_orchestrator_enabled(deps: Any) -> Config:
    """Return the live :class:`Config` after asserting orchestrator mode.

    Returns 503 with ``code: "orchestrator_disabled"`` per the spec when
    ``config.orchestrator.enabled`` is False (or when no config is wired).
    """
    config = getattr(deps, "config", None)
    if config is None or not config.orchestrator.enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "orchestrator_disabled",
                "message": (
                    "the orchestrator is not enabled on this workstation; "
                    "set orchestrator.enabled to true in config.yaml to use "
                    "this endpoint"
                ),
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
