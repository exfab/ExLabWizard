"""Unit tests for the ``/operations`` router."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from exlab_wizard.api import AppDependencies, create_app
from exlab_wizard.config.models import (
    Config,
    EquipmentConfig,
    LIMSConfig,
    OrchestratorConfig,
    PathsConfig,
    RcloneTransport,
)
from exlab_wizard.controller.creation import ProjectCreateRequest
from exlab_wizard.controller.session_store import SessionStore
from exlab_wizard.controller.state_machine import SessionState


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
        orchestrator=OrchestratorConfig(label="LAB", staging_root="/staging"),
    )


class _StubController:
    def __init__(self) -> None:
        self.session_store = SessionStore()


def _make_project_request(short_id: str = "PROJ-0042") -> ProjectCreateRequest:
    return ProjectCreateRequest(
        equipment_id="EQ1",
        template_path=Path("/tpl"),
        lims_project={
            "uid": "u",
            "short_id": short_id,
            "name_at_creation": "x",
            "source": "live",
        },
        variables={},
        label="label",
        operator="op",
        objective="obj",
    )


def test_get_operations_lists_in_flight_sessions() -> None:
    controller = _StubController()
    s1 = controller.session_store.open("project", _make_project_request("PROJ-0001"))
    controller.session_store.transition(s1.session_id, SessionState.VALIDATING)
    s2 = controller.session_store.open("project", _make_project_request("PROJ-0002"))
    controller.session_store.transition(s2.session_id, SessionState.VALIDATING)
    deps = AppDependencies(config=_ready_config(), controller=controller)
    app = create_app(dependencies=deps)
    client = TestClient(app)
    response = client.get("/api/v1/operations")
    assert response.status_code == 200
    body = response.json()
    assert len(body["operations"]) == 2
    short_ids = {op["project_short_id"] for op in body["operations"]}
    assert short_ids == {"PROJ-0001", "PROJ-0002"}


def test_get_operations_excludes_done_and_aborted() -> None:
    controller = _StubController()
    s1 = controller.session_store.open("project", _make_project_request())
    controller.session_store.transition(s1.session_id, SessionState.VALIDATING)
    controller.session_store.transition(s1.session_id, SessionState.RENDERING)
    controller.session_store.transition(s1.session_id, SessionState.PLUGIN_PASS)
    controller.session_store.transition(s1.session_id, SessionState.CACHE_WRITE)
    controller.session_store.transition(s1.session_id, SessionState.POST_VALIDATE)
    controller.session_store.transition(s1.session_id, SessionState.SYNC_QUEUED)
    controller.session_store.transition(s1.session_id, SessionState.DONE)
    s2 = controller.session_store.open("project", _make_project_request())
    controller.session_store.transition(s2.session_id, SessionState.VALIDATING)
    controller.session_store.transition(s2.session_id, SessionState.ABORTED)
    deps = AppDependencies(config=_ready_config(), controller=controller)
    app = create_app(dependencies=deps)
    client = TestClient(app)
    body = client.get("/api/v1/operations").json()
    assert body["operations"] == []


def test_get_operations_emits_plugin_name_when_input_required() -> None:
    controller = _StubController()
    s = controller.session_store.open("project", _make_project_request())
    controller.session_store.transition(s.session_id, SessionState.VALIDATING)
    controller.session_store.transition(s.session_id, SessionState.RENDERING)
    controller.session_store.transition(s.session_id, SessionState.PLUGIN_PASS)
    controller.session_store.transition(s.session_id, SessionState.INPUT_REQUIRED)
    s.pending_input = {"plugin": "xlsx_field_filler", "reason": "need a value"}
    deps = AppDependencies(config=_ready_config(), controller=controller)
    app = create_app(dependencies=deps)
    client = TestClient(app)
    body = client.get("/api/v1/operations").json()
    assert len(body["operations"]) == 1
    assert body["operations"][0]["plugin_name"] == "xlsx_field_filler"
    assert body["operations"][0]["suspended_reason"] == "need a value"


def test_get_operations_503_when_controller_missing() -> None:
    deps = AppDependencies(config=_ready_config())
    app = create_app(dependencies=deps)
    client = TestClient(app)
    response = client.get("/api/v1/operations")
    assert response.status_code == 503
