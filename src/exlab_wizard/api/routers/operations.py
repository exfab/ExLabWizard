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

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict

from exlab_wizard.api._dependencies import require_controller
from exlab_wizard.api.setup import setup_state_gate

# Import from the submodules (not the ``exlab_wizard.controller`` package)
# to avoid a circular import: ``api.app`` pulls in this router while the
# controller package's ``__init__`` is still initializing, so reading
# attributes off the partially-built package would fail.
from exlab_wizard.controller.session_store import on_operations_panel, project_identifier
from exlab_wizard.controller.state_machine import SessionState
from exlab_wizard.utils.time import dt_to_iso

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
        controller = require_controller(request)
        sessions = controller.session_store
        operations: list[OperationEntry] = []
        for sid, session in sessions.iter_sorted():
            # Terminal-success and explicit-cancel rows fall off the panel;
            # FAILED rows stay so the operator can see the recent failure
            # (Frontend §9.5 -- the shared membership rule).
            if not on_operations_panel(session):
                continue
            operations.append(_session_to_entry(sid, session))
        return OperationsResponse(operations=operations)

    return router


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
        started_at=dt_to_iso(session.created_at) if session.created_at is not None else "",
        equipment_id=getattr(request, "equipment_id", None),
        project_short_id=project_identifier(request),
        run_label=getattr(request, "label", None),
        plugin_name=plugin_name,
        suspended_reason=suspended_reason,
    )
