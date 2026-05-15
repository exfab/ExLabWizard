"""``/problems`` router. Backend Spec §4.6.1, §11.8.

Endpoints:

* ``GET /problems`` -- query findings with optional scope/severity/class.
* ``POST /problems/{run_path}/override`` -- append an override entry.
* ``POST /problems/{run_path}/override/revoke`` -- append a tombstone.
* ``POST /problems/refresh`` -- re-run ``Validator.audit("all")``.
* ``WS /problems/events`` -- subscribe to the audit pub-sub channel.

The router dispatches to the bound :class:`Validator`, the
:class:`CreationWriter` for override mutations, and the
``audit_channel`` pub-sub object that the lifespan handler attaches.
"""

from __future__ import annotations

import contextlib
import uuid
from pathlib import Path
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from pydantic import BaseModel, ConfigDict, Field

from exlab_wizard.api._dependencies import require_deps
from exlab_wizard.api.events import encode_event, event_from_dict
from exlab_wizard.api.schemas import (
    OverrideEntry,
    TombstoneEntry,
    override_entry_to_dict,
    tombstone_entry_to_dict,
)
from exlab_wizard.api.setup import setup_state_gate
from exlab_wizard.constants import AuditScopeKind
from exlab_wizard.logging import get_logger
from exlab_wizard.paths import creation_json_path
from exlab_wizard.utils.time import utc_now_iso

__all__ = [
    "FindingResponse",
    "OverrideRequest",
    "OverrideResponse",
    "ProblemsResponse",
    "RefreshResponse",
    "RevokeRequest",
    "build_problems_router",
]

_log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class FindingResponse(BaseModel):
    """One finding row in the §11.8 schema."""

    model_config = ConfigDict(extra="forbid")

    rule: str
    tier: str
    run_path: str
    offending_path: str
    offending_kind: str
    matched_token: str | None = None
    rule_detail: str = ""
    synced_under_prior_policy: bool = False
    override_active: bool = False


class ProblemsResponse(BaseModel):
    """``GET /problems`` response."""

    model_config = ConfigDict(extra="forbid")

    findings: list[FindingResponse]
    audit_at: str


class OverrideRequest(BaseModel):
    """``POST /problems/{run_path}/override`` body. Backend Spec §11.3."""

    model_config = ConfigDict(extra="forbid")

    problem_class: str
    reason: str = Field(min_length=10, max_length=500)
    operator: str = ""
    expires_at: str | None = None


class OverrideResponse(BaseModel):
    """Override append response."""

    model_config = ConfigDict(extra="forbid")

    id: str
    problem_class: str
    operator: str
    recorded_at: str
    reason: str
    revoked: bool
    expires_at: str | None = None


class RevokeRequest(BaseModel):
    """``POST /problems/{run_path}/override/revoke`` body. Backend Spec §11.3."""

    model_config = ConfigDict(extra="forbid")

    revokes: str
    reason: str = Field(min_length=10, max_length=500)
    operator: str = ""


class TombstoneResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    revokes: str
    operator: str
    recorded_at: str
    reason: str
    revoked: bool


class RefreshResponse(BaseModel):
    """``POST /problems/refresh`` response."""

    model_config = ConfigDict(extra="forbid")

    audit_at: str
    finding_count: int


# ---------------------------------------------------------------------------
# Router builder
# ---------------------------------------------------------------------------


def build_problems_router() -> APIRouter:
    """Construct the ``/problems`` router."""
    router = APIRouter(prefix="/problems", tags=["problems"])

    @router.get(
        "",
        response_model=ProblemsResponse,
        dependencies=[Depends(setup_state_gate)],
    )
    async def list_problems(
        request: Request,
        scope: str = Query("all"),
        scope_value: str | None = Query(None),
        severity: str | None = Query(None),
        problem_class: str | None = Query(None, alias="class"),
    ) -> ProblemsResponse:
        validator = _require_validator(request)
        scope_arg: dict[str, Any] = _build_scope(scope, scope_value)
        findings = validator.query_problems(scope_arg)
        rows = [
            FindingResponse(**finding.to_dict())
            for finding in findings
            if _matches_filters(finding, severity=severity, problem_class=problem_class)
        ]
        audit_at = _last_audit_at(request)
        return ProblemsResponse(findings=rows, audit_at=audit_at)

    @router.post(
        "/refresh",
        response_model=RefreshResponse,
        dependencies=[Depends(setup_state_gate)],
    )
    async def refresh(request: Request) -> RefreshResponse:
        validator = _require_validator(request)
        findings = validator.audit({"kind": AuditScopeKind.ALL})
        audit_at = utc_now_iso()
        deps = require_deps(request)
        deps.last_audit_at = audit_at
        # Publish an audit-pass snapshot if a channel is wired.
        channel = getattr(deps, "audit_channel", None)
        if channel is not None:
            with contextlib.suppress(Exception):
                await channel.publish_snapshot(findings, audit_at)
        return RefreshResponse(audit_at=audit_at, finding_count=len(findings))

    @router.post(
        "/{run_path:path}/override",
        response_model=OverrideResponse,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(setup_state_gate)],
    )
    async def append_override(
        request: Request, run_path: str, body: OverrideRequest
    ) -> OverrideResponse:
        cache_writer = _require_cache_writer(request)
        path = _require_creation_json(run_path)
        entry = OverrideEntry(
            id=str(uuid.uuid4()),
            problem_class=body.problem_class,
            operator=body.operator,
            recorded_at=utc_now_iso(),
            reason=body.reason,
            expires_at=body.expires_at,
        )
        entry_dict = override_entry_to_dict(entry)

        def _mutate(payload: Any) -> Any:
            payload.validation_overrides = [*payload.validation_overrides, dict(entry_dict)]
            return payload

        await cache_writer.update_creation_atomic(path, _mutate)
        return OverrideResponse(**entry_dict)

    @router.post(
        "/{run_path:path}/override/revoke",
        response_model=TombstoneResponse,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(setup_state_gate)],
    )
    async def revoke_override(
        request: Request, run_path: str, body: RevokeRequest
    ) -> TombstoneResponse:
        cache_writer = _require_cache_writer(request)
        path = _require_creation_json(run_path)
        entry = TombstoneEntry(
            id=str(uuid.uuid4()),
            revokes=body.revokes,
            operator=body.operator,
            recorded_at=utc_now_iso(),
            reason=body.reason,
        )
        entry_dict = tombstone_entry_to_dict(entry)

        def _mutate(payload: Any) -> Any:
            payload.validation_overrides = [*payload.validation_overrides, dict(entry_dict)]
            return payload

        await cache_writer.update_creation_atomic(path, _mutate)
        return TombstoneResponse(**entry_dict)

    @router.websocket("/events")
    async def problems_events(websocket: WebSocket) -> None:
        deps = getattr(websocket.app.state, "dependencies", None)
        channel = getattr(deps, "audit_channel", None) if deps else None
        if channel is None:
            await websocket.close(code=1011, reason="audit channel not initialized")
            return
        await websocket.accept()
        validator = getattr(deps, "validator", None)
        if validator is not None:
            findings = validator.audit({"kind": AuditScopeKind.ALL})
            audit_at = getattr(deps, "last_audit_at", None) or utc_now_iso()
            await websocket.send_bytes(
                encode_event(
                    event_from_dict(
                        {
                            "kind": "snapshot",
                            "findings": [f.to_dict() for f in findings],
                            "audit_at": audit_at,
                        }
                    )
                )
            )
        try:
            await _stream_audit_channel(websocket, channel)
        except WebSocketDisconnect:
            return
        except Exception as exc:
            _log.warning("problems_events stream error: %s", exc)
            with contextlib.suppress(Exception):
                await websocket.close(code=1011, reason="stream_error")

    return router


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_scope(scope: AuditScopeKind | str, scope_value: str | None) -> dict[str, Any]:
    try:
        kind = AuditScopeKind(scope) if not isinstance(scope, AuditScopeKind) else scope
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "validation_failed",
                "message": f"unknown scope {scope!r}",
                "field": "scope",
            },
        ) from exc
    if kind is AuditScopeKind.ALL:
        return {"kind": kind.value}
    if kind is AuditScopeKind.EQUIPMENT_ID:
        return {"kind": kind.value, "value": scope_value or ""}
    if kind is AuditScopeKind.PROJECT_PATH:
        return {"kind": kind.value, "value": scope_value or ""}
    # Unreachable: AuditScopeKind has only the three members above.
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={
            "code": "validation_failed",
            "message": f"unknown scope {scope!r}",
            "field": "scope",
        },
    )


def _matches_filters(finding: Any, *, severity: str | None, problem_class: str | None) -> bool:
    if severity is not None and finding.tier != severity:
        return False
    return not (problem_class is not None and finding.rule != problem_class)


def _last_audit_at(request: Request) -> str:
    deps = getattr(request.app.state, "dependencies", None)
    if deps is None:
        return utc_now_iso()
    return getattr(deps, "last_audit_at", None) or utc_now_iso()


def _require_validator(request: Request) -> Any:
    deps = require_deps(request)
    validator = getattr(deps, "validator", None)
    if validator is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "internal_error",
                "message": "validator is not initialized",
            },
        )
    return validator


def _require_cache_writer(request: Request) -> Any:
    deps = require_deps(request)
    writer = getattr(deps, "cache_creation", None)
    if writer is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "internal_error",
                "message": "cache writer is not initialized",
            },
        )
    return writer


def _require_creation_json(run_path: str) -> Path:
    """Resolve a ``run_path`` to its ``creation.json`` or raise 404.

    Used by the override-append and override-revoke routes; both need
    the same "the run directory must exist on disk and have a
    ``creation.json`` cache file" gate before they can mutate the file.
    """
    path = creation_json_path(Path(run_path))
    if not path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "session_not_found",
                "message": f"creation.json not found at {path}",
            },
        )
    return path


async def _stream_audit_channel(websocket: WebSocket, channel: Any) -> None:
    """Forward each delta frame the audit channel publishes.

    The channel exposes an async iterator via ``subscribe()`` returning
    dicts shaped per :func:`event_from_dict`.
    """
    async for frame in channel.subscribe():
        try:
            typed = event_from_dict(frame)
        except ValueError:
            continue
        await websocket.send_bytes(encode_event(typed))
