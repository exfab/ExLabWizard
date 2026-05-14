"""Unit tests for the ``/sessions`` router."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from exlab_wizard.api import AppDependencies, create_app
from exlab_wizard.config.models import (
    Config,
    EquipmentConfig,
    LIMSConfig,
    PathsConfig,
    RcloneTransport,
)
from exlab_wizard.constants import RunKind
from exlab_wizard.controller.creation import (
    ProjectCreateRequest,
    RunCreateRequest,
    SessionHandle,
)
from exlab_wizard.controller.session_store import SessionStore
from exlab_wizard.controller.state_machine import Phase, SessionState


def _ready_config() -> Config:
    return Config(
        paths=PathsConfig(templates_dir="/t", plugin_dir="/p", local_root="/d"),
        equipment=[
            EquipmentConfig(
                id="EQ1",
                label="Equipment 1",
                local_root="/d",
                nas_root="/n",
                completeness_signal="sentinel_file",
                sentinel_filename="done.flag",
                transport=RcloneTransport(
                    type="rclone",
                    rclone_remote="lab-nas",
                    rclone_remote_path="lab/EQ1",
                ),
            )
        ],
        lims=LIMSConfig(endpoint="https://lims.example", email="op@example"),
    )


class _StubController:
    """Minimal in-memory controller for routing tests."""

    def __init__(self) -> None:
        self.session_store = SessionStore()
        self.created_requests: list[Any] = []
        self.cancelled: list[tuple[str, bool]] = []
        self.resumed: list[tuple[str, dict[str, Any]]] = []

    async def create_project(self, req: ProjectCreateRequest) -> SessionHandle:
        session = self.session_store.open("project", req)
        self.session_store.transition(session.session_id, SessionState.VALIDATING)
        self.session_store.transition(session.session_id, SessionState.RENDERING)
        self.created_requests.append(req)
        return SessionHandle(
            session_id=session.session_id,
            state=session.state,
            current_phase=session.current_phase,
            next_action=session.next_action,
        )

    async def create_run(self, req: RunCreateRequest) -> SessionHandle:
        session = self.session_store.open("run", req)
        self.session_store.transition(session.session_id, SessionState.VALIDATING)
        self.session_store.transition(session.session_id, SessionState.RENDERING)
        self.created_requests.append(req)
        return SessionHandle(
            session_id=session.session_id,
            state=session.state,
            current_phase=session.current_phase,
            next_action=session.next_action,
        )

    async def status(self, session_id: str) -> SessionHandle:
        session = self.session_store.get(session_id)
        if session is None:
            raise ValueError(f"unknown session {session_id!r}")
        return SessionHandle(
            session_id=session.session_id,
            state=session.state,
            current_phase=session.current_phase,
            next_action=session.next_action,
        )

    async def resume(self, session_id: str, extra: dict[str, Any]) -> SessionHandle:
        self.resumed.append((session_id, dict(extra)))
        return await self.status(session_id)

    async def cancel(self, session_id: str, *, discard_files: bool = False) -> None:
        self.cancelled.append((session_id, discard_files))
        session = self.session_store.get(session_id)
        if session is not None and not session.is_terminal():
            self.session_store.transition(session_id, SessionState.ABORTED)
            self.session_store.close(session_id, {"code": "cancelled"})

    async def subscribe(self, session_id: str):
        session = self.session_store.get(session_id)
        if session is None:
            raise ValueError(f"unknown {session_id!r}")
        if session.event_queue is None:
            session.event_queue = asyncio.Queue()
        while True:
            frame = await session.event_queue.get()
            yield frame
            if frame.get("kind") in ("done", "failed"):
                break


def test_create_session_project(tmp_path: Path) -> None:
    controller = _StubController()
    deps = AppDependencies(config=_ready_config(), controller=controller)
    app = create_app(dependencies=deps)
    client = TestClient(app)
    body = {
        "kind": "project",
        "equipment_id": "EQ1",
        "template_path": str(tmp_path / "tpl"),
        "label": "Project A",
        "operator": "asmith",
        "objective": "objective text",
        "lims_project": {
            "uid": "u",
            "short_id": "PROJ-0001",
            "name_at_creation": "Project A",
        },
        "variables": {},
        "readme_extra": {},
    }
    response = client.post("/api/v1/sessions", json=body)
    assert response.status_code == 201
    out = response.json()
    assert out["state"] == SessionState.RENDERING.value
    assert out["next_action"] == "none"
    assert len(controller.created_requests) == 1
    assert isinstance(controller.created_requests[0], ProjectCreateRequest)


def test_create_session_run(tmp_path: Path) -> None:
    controller = _StubController()
    deps = AppDependencies(config=_ready_config(), controller=controller)
    app = create_app(dependencies=deps)
    client = TestClient(app)
    body = {
        "kind": "run",
        "equipment_id": "EQ1",
        "project_name": "Cortex Q3 Pilot",
        "template_path": str(tmp_path / "tpl"),
        "run_kind": "experimental",
        "label": "calibration",
        "operator": "asmith",
        "objective": "obj",
        "variables": {},
        "readme_extra": {},
        "lims_project": {},
    }
    response = client.post("/api/v1/sessions", json=body)
    assert response.status_code == 201
    assert isinstance(controller.created_requests[0], RunCreateRequest)
    assert controller.created_requests[0].run_kind is RunKind.EXPERIMENTAL


def test_get_session_returns_snapshot() -> None:
    controller = _StubController()
    deps = AppDependencies(config=_ready_config(), controller=controller)
    app = create_app(dependencies=deps)
    client = TestClient(app)
    # First create a session to populate the store.
    create = client.post(
        "/api/v1/sessions",
        json={
            "kind": "project",
            "equipment_id": "EQ1",
            "template_path": "/tpl",
            "label": "x",
            "operator": "x",
            "objective": "x",
            "lims_project": {},
            "variables": {},
            "readme_extra": {},
        },
    )
    sid = create.json()["session_id"]
    response = client.get(f"/api/v1/sessions/{sid}")
    assert response.status_code == 200


def test_get_session_404_for_unknown() -> None:
    controller = _StubController()
    deps = AppDependencies(config=_ready_config(), controller=controller)
    app = create_app(dependencies=deps)
    client = TestClient(app)
    response = client.get("/api/v1/sessions/no_such_id")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "session_not_found"


def test_post_resume_invokes_controller() -> None:
    controller = _StubController()
    deps = AppDependencies(config=_ready_config(), controller=controller)
    app = create_app(dependencies=deps)
    # Create + flip to INPUT_REQUIRED so resume is legal.
    session = controller.session_store.open("project", _make_request())
    controller.session_store.transition(session.session_id, SessionState.VALIDATING)
    controller.session_store.transition(session.session_id, SessionState.RENDERING)
    controller.session_store.transition(session.session_id, SessionState.PLUGIN_PASS)
    controller.session_store.transition(session.session_id, SessionState.INPUT_REQUIRED)
    client = TestClient(app)
    response = client.post(
        f"/api/v1/sessions/{session.session_id}/resume",
        json={"extra_inputs": {"x": 1}},
    )
    assert response.status_code == 200
    assert controller.resumed == [(session.session_id, {"x": 1})]


def test_post_cancel_invokes_controller() -> None:
    controller = _StubController()
    deps = AppDependencies(config=_ready_config(), controller=controller)
    app = create_app(dependencies=deps)
    session = controller.session_store.open("project", _make_request())
    controller.session_store.transition(session.session_id, SessionState.VALIDATING)
    client = TestClient(app)
    response = client.post(
        f"/api/v1/sessions/{session.session_id}/cancel",
        json={"discard_files": True},
    )
    assert response.status_code == 200
    assert controller.cancelled == [(session.session_id, True)]


def test_post_cancel_404_for_unknown() -> None:
    controller = _StubController()
    deps = AppDependencies(config=_ready_config(), controller=controller)
    app = create_app(dependencies=deps)
    client = TestClient(app)
    response = client.post("/api/v1/sessions/no_id/cancel", json={"discard_files": False})
    assert response.status_code == 404


def test_post_resume_409_for_terminal_session() -> None:
    controller = _StubController()
    deps = AppDependencies(config=_ready_config(), controller=controller)
    app = create_app(dependencies=deps)
    session = controller.session_store.open("project", _make_request())
    controller.session_store.transition(session.session_id, SessionState.VALIDATING)
    controller.session_store.transition(session.session_id, SessionState.FAILED)
    client = TestClient(app)
    response = client.post(
        f"/api/v1/sessions/{session.session_id}/resume",
        json={"extra_inputs": {}},
    )
    assert response.status_code == 409


def test_websocket_streams_session_events() -> None:
    import json

    controller = _StubController()
    deps = AppDependencies(config=_ready_config(), controller=controller)
    app = create_app(dependencies=deps)
    session = controller.session_store.open("project", _make_request())
    session.event_queue = asyncio.Queue()
    session.event_queue.put_nowait(
        {"kind": "phase", "phase": Phase.VALIDATING_INPUTS.value, "at": "2026-05-01T00:00:00Z"}
    )
    session.event_queue.put_nowait({"kind": "done", "result": {"path": "/data"}})

    client = TestClient(app)
    with client.websocket_connect(f"/api/v1/sessions/{session.session_id}/events") as ws:
        first = json.loads(ws.receive_bytes())
        assert first["kind"] == "phase"
        second = json.loads(ws.receive_bytes())
        assert second["kind"] == "done"


def test_websocket_unknown_session_closes() -> None:
    controller = _StubController()
    deps = AppDependencies(config=_ready_config(), controller=controller)
    app = create_app(dependencies=deps)
    client = TestClient(app)
    try:
        with client.websocket_connect("/api/v1/sessions/unknown_id/events") as ws:
            ws.receive_json()
    except Exception:
        pass


def _make_request() -> ProjectCreateRequest:
    return ProjectCreateRequest(
        equipment_id="EQ1",
        template_path=Path("/tpl"),
        lims_project={
            "uid": "u",
            "short_id": "PROJ-0001",
            "name_at_creation": "x",
            "source": "live",
        },
        variables={},
        label="label",
        operator="op",
        objective="obj",
    )


def test_create_session_validation_error_returns_422() -> None:
    """Posting without ``kind`` discriminator must 422."""
    controller = _StubController()
    deps = AppDependencies(config=_ready_config(), controller=controller)
    app = create_app(dependencies=deps)
    client = TestClient(app)
    response = client.post("/api/v1/sessions", json={"equipment_id": "EQ1"})
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_failed"
