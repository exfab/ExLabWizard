"""``/sessions`` router. Backend Spec §4.6.1, §4.6.2.

Endpoints:

* ``POST /sessions`` -- open a creation session (project or run).
* ``GET /sessions/{id}`` -- snapshot of session state.
* ``POST /sessions/{id}/resume`` -- supply ``extra_inputs`` after a
  ``PluginInputRequired`` prompt.
* ``POST /sessions/{id}/cancel`` -- abort the session, optionally
  discarding the partial directory.
* ``WS /sessions/{id}/events`` -- per-session event channel
  (§4.6.2 envelope types live in ``api/events.py``).

The router consumes :class:`AppDependencies` via ``request.app.state``;
in production the launcher constructs the dependencies, in tests the
fixture passes a stub. Setup-state gating is applied via
:func:`api.setup.setup_state_gate` on the routes that need a complete
config.
"""

from __future__ import annotations

import contextlib
from datetime import datetime
from pathlib import Path
from typing import Any, Final, Literal

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from pydantic import BaseModel, ConfigDict, Field

from exlab_wizard.api.events import encode_event, event_from_dict
from exlab_wizard.api.setup import setup_state_gate
from exlab_wizard.constants import RunKind
from exlab_wizard.controller import (
    ProjectCreateRequest,
    RunCreateRequest,
    SessionState,
)
from exlab_wizard.logging import get_logger
from exlab_wizard.utils.time import parse_utc_iso

__all__ = ["build_sessions_router"]

_log = get_logger(__name__)

TERMINAL_EVENT_KINDS: Final[frozenset[str]] = frozenset({"done", "failed"})


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class _ProjectSessionBody(BaseModel):
    """Body for a project-creation session.

    The ``kind`` discriminator selects this shape vs. the run shape on
    the same endpoint.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["project"]
    equipment_id: str
    template_path: str
    label: str
    operator: str
    objective: str
    lims_project: dict[str, Any] = Field(default_factory=dict)
    variables: dict[str, Any] = Field(default_factory=dict)
    readme_extra: dict[str, Any] = Field(default_factory=dict)


class _RunSessionBody(BaseModel):
    """Body for a run / test-run creation session."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["run"]
    equipment_id: str
    project_short_id: str
    template_path: str
    run_kind: RunKind
    label: str
    operator: str
    objective: str
    variables: dict[str, Any] = Field(default_factory=dict)
    readme_extra: dict[str, Any] = Field(default_factory=dict)
    lims_project: dict[str, Any] = Field(default_factory=dict)
    run_date: str | None = None


SessionCreateRequest = _ProjectSessionBody | _RunSessionBody


class SessionHandleResponse(BaseModel):
    """Response shape for the create / status endpoints."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    state: str
    current_phase: str | None = None
    next_action: str
    pending_input: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    result: dict[str, Any] | None = None


class _ResumeBody(BaseModel):
    """``POST /sessions/{id}/resume`` body."""

    model_config = ConfigDict(extra="forbid")

    extra_inputs: dict[str, Any] = Field(default_factory=dict)


class _CancelBody(BaseModel):
    """``POST /sessions/{id}/cancel`` body."""

    model_config = ConfigDict(extra="forbid")

    discard_files: bool = False


# ---------------------------------------------------------------------------
# Router builder
# ---------------------------------------------------------------------------


def build_sessions_router() -> APIRouter:
    """Construct the ``/sessions`` router.

    Routes are gated by the setup-state dependency except where
    explicitly noted. WebSocket routes don't accept dependencies in the
    standard FastAPI way, so the gate is applied inline.
    """
    router = APIRouter(prefix="/sessions", tags=["sessions"])

    @router.post(
        "",
        response_model=SessionHandleResponse,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(setup_state_gate)],
    )
    async def create_session(
        request: Request,
        body: SessionCreateRequest,
    ) -> SessionHandleResponse:
        controller = _require_controller(request)
        if isinstance(body, _ProjectSessionBody):
            handle = await controller.create_project(_build_project_request(body))
        else:
            handle = await controller.create_run(_build_run_request(body))
        session = controller.session_store.get(handle.session_id)
        return _handle_to_response(handle, session)

    @router.get("/{session_id}", response_model=SessionHandleResponse)
    async def get_session(request: Request, session_id: str) -> SessionHandleResponse:
        controller = _require_controller(request)
        session = controller.session_store.get(session_id)
        if session is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "session_not_found",
                    "message": f"unknown session {session_id!r}",
                },
            )
        try:
            handle = await controller.status(session_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "session_not_found",
                    "message": str(exc),
                },
            ) from exc
        return _handle_to_response(handle, session)

    @router.post(
        "/{session_id}/resume",
        response_model=SessionHandleResponse,
        dependencies=[Depends(setup_state_gate)],
    )
    async def resume_session(
        request: Request,
        session_id: str,
        body: _ResumeBody,
    ) -> SessionHandleResponse:
        controller = _require_controller(request)
        session = controller.session_store.get(session_id)
        if session is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "session_not_found",
                    "message": f"unknown session {session_id!r}",
                },
            )
        if session.is_terminal():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "session_already_completed",
                    "message": "session is already in a terminal state",
                },
            )
        handle = await controller.resume(session_id, body.extra_inputs)
        return _handle_to_response(handle, session)

    @router.post(
        "/{session_id}/cancel",
        response_model=SessionHandleResponse,
        dependencies=[Depends(setup_state_gate)],
    )
    async def cancel_session(
        request: Request,
        session_id: str,
        body: _CancelBody,
    ) -> SessionHandleResponse:
        controller = _require_controller(request)
        session = controller.session_store.get(session_id)
        if session is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "session_not_found",
                    "message": f"unknown session {session_id!r}",
                },
            )
        if session.is_terminal():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "session_already_completed",
                    "message": "session is already in a terminal state",
                },
            )
        await controller.cancel(session_id, discard_files=body.discard_files)
        handle = await controller.status(session_id)
        return _handle_to_response(handle, session)

    @router.websocket("/{session_id}/events")
    async def session_events(websocket: WebSocket, session_id: str) -> None:
        """Stream per-session event frames. Backend Spec §4.6.2."""
        controller = getattr(websocket.app.state, "dependencies", None)
        controller = getattr(controller, "controller", None) if controller else None
        if controller is None:
            await websocket.close(code=1011, reason="controller not initialized")
            return
        session = controller.session_store.get(session_id)
        if session is None:
            await websocket.close(code=4404, reason="session_not_found")
            return
        await websocket.accept()
        try:
            await _stream_session_events(websocket, controller, session_id)
        except WebSocketDisconnect:
            return
        except Exception as exc:
            _log.warning("session_events stream error: %s", exc)
            with contextlib.suppress(Exception):
                await websocket.close(code=1011, reason="stream_error")

    return router


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_controller(request: Request) -> Any:
    """Pluck the controller out of the bound :class:`AppDependencies`.

    503 if the lifespan handler did not initialize dependencies; callers
    that need this on every route get a clear error.
    """
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


def _build_project_request(body: _ProjectSessionBody) -> ProjectCreateRequest:
    return ProjectCreateRequest(
        equipment_id=body.equipment_id,
        template_path=Path(body.template_path),
        lims_project=dict(body.lims_project),
        variables=dict(body.variables),
        label=body.label,
        operator=body.operator,
        objective=body.objective,
        readme_extra=dict(body.readme_extra),
    )


def _build_run_request(body: _RunSessionBody) -> RunCreateRequest:
    run_date: datetime | None = None
    if body.run_date:
        run_date = parse_utc_iso(body.run_date)
    return RunCreateRequest(
        equipment_id=body.equipment_id,
        project_short_id=body.project_short_id,
        template_path=Path(body.template_path),
        run_kind=RunKind(body.run_kind),
        variables=dict(body.variables),
        label=body.label,
        operator=body.operator,
        objective=body.objective,
        readme_extra=dict(body.readme_extra),
        run_date=run_date,
        lims_project=dict(body.lims_project),
    )


def _handle_to_response(handle: Any, session: Any) -> SessionHandleResponse:
    """Build the response model from a controller :class:`SessionHandle` and the live session."""
    state_value = (
        handle.state.value if isinstance(handle.state, SessionState) else str(handle.state)
    )
    phase_value = handle.current_phase.value if handle.current_phase is not None else None
    pending = session.pending_input if session is not None else None
    error = session.error if session is not None else None
    result = session.result if session is not None else None
    return SessionHandleResponse(
        session_id=handle.session_id,
        state=state_value,
        current_phase=phase_value,
        next_action=handle.next_action,
        pending_input=pending,
        error=error,
        result=result,
    )


async def _stream_session_events(
    websocket: WebSocket,
    controller: Any,
    session_id: str,
) -> None:
    """Pump events from the controller's queue into the WebSocket.

    Each frame from the controller's ``subscribe`` iterator is
    round-tripped through :func:`event_from_dict` (so the wire shape is
    spec-checked) and encoded via ``msgspec.json.encode``.
    """
    async for frame in controller.subscribe(session_id):
        try:
            typed = event_from_dict(frame)
        except ValueError:
            # Unknown frame -- skip but keep the stream alive. The
            # controller is the source of frames and we tolerate
            # forward-compat additions.
            continue
        await websocket.send_bytes(encode_event(typed))
        kind = frame.get("kind")
        if kind in TERMINAL_EVENT_KINDS:
            break
    with contextlib.suppress(Exception):
        await websocket.close()


# Re-export the request union so app.py can register it on the OpenAPI surface.
__all__ = ["SessionCreateRequest", "SessionHandleResponse", "build_sessions_router"]
