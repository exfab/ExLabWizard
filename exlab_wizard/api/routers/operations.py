"""``/operations`` router. Backend Spec §4.6.1, Frontend §9.5.

Lists all in-flight controller operations. The Frontend's Operations
panel (Frontend §9.5) renders one entry per session: ``id``, ``state``,
``started_at``, ``equipment_id``, ``project_short_id``, ``run_label``,
optional ``plugin_name`` (when in ``INPUT_REQUIRED``), and optional
``suspended_reason`` (the reason string from the
``PluginInputRequired`` payload).

The endpoint reads the in-memory :class:`SessionStore` directly via
the controller; non-terminal sessions are returned in chronological
order so the panel is stable across refreshes.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict

from exlab_wizard.api.setup import setup_state_gate
from exlab_wizard.controller import SessionState

__all__ = ["OperationEntry", "OperationsResponse", "build_operations_router"]


class OperationEntry(BaseModel):
    """One row in the Operations panel. Backend Spec §4.6.1, Frontend §9.5."""

    model_config = ConfigDict(extra="forbid")

    id: str
    state: str
    started_at: str
    equipment_id: str | None = None
    project_short_id: str | None = None
    run_label: str | None = None
    plugin_name: str | None = None
    suspended_reason: str | None = None


class OperationsResponse(BaseModel):
    """``GET /operations`` response."""

    model_config = ConfigDict(extra="forbid")

    operations: list[OperationEntry]


def build_operations_router() -> APIRouter:
    """Construct the ``/operations`` router."""
    router = APIRouter(tags=["operations"])

    @router.get(
        "/operations",
        response_model=OperationsResponse,
        dependencies=[Depends(setup_state_gate)],
    )
    async def list_operations(request: Request) -> OperationsResponse:
        controller = _require_controller(request)
        sessions = controller.session_store
        operations: list[OperationEntry] = []
        # SessionStore exposes a private ``_sessions`` dict; iterate
        # explicitly rather than reaching into the dict so the public
        # surface stays narrow.
        for sid, session in _iter_sessions(sessions):
            if session.state in (SessionState.DONE, SessionState.ABORTED):
                # Terminal-success and explicit-cancel rows fall off
                # the panel; FAILED rows stay so the operator can see
                # the recent failure.
                continue
            operations.append(_session_to_entry(sid, session))
        return OperationsResponse(operations=operations)

    return router


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_controller(request: Request) -> Any:
    deps = getattr(request.app.state, "dependencies", None)
    controller = getattr(deps, "controller", None) if deps else None
    if controller is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "internal_error",
                "message": "controller is not initialized",
            },
        )
    return controller


def _iter_sessions(store: Any) -> list[tuple[str, Any]]:
    """Return ``(session_id, session)`` pairs from the store.

    The :class:`SessionStore` keeps its dict private; we use the
    documented contract that ``store._sessions`` is a ``dict``. A
    public accessor would be cleaner; until that lands the shim here
    is the single touchpoint.
    """
    sessions = getattr(store, "_sessions", {})
    if not isinstance(sessions, dict):
        return []
    return sorted(
        sessions.items(),
        key=lambda pair: getattr(pair[1], "created_at", None) or 0,
    )


def _session_to_entry(session_id: str, session: Any) -> OperationEntry:
    request = session.request
    plugin_name: str | None = None
    suspended_reason: str | None = None
    if session.pending_input is not None:
        plugin_name = session.pending_input.get("plugin")
        suspended_reason = session.pending_input.get("reason")
    return OperationEntry(
        id=session_id,
        state=session.state.value
        if isinstance(session.state, SessionState)
        else str(session.state),
        started_at=session.created_at.isoformat() if session.created_at is not None else "",
        equipment_id=getattr(request, "equipment_id", None),
        project_short_id=_project_short_id(request),
        run_label=getattr(request, "label", None),
        plugin_name=plugin_name,
        suspended_reason=suspended_reason,
    )


def _project_short_id(request: Any) -> str | None:
    """Pluck the project short id off a project / run request."""
    short = getattr(request, "project_short_id", None)
    if short:
        return short
    lims_project = getattr(request, "lims_project", None)
    if isinstance(lims_project, dict):
        value = lims_project.get("short_id")
        return value if isinstance(value, str) and value else None
    return None
