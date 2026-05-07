"""Unit tests for ``exlab_wizard.api.setup`` (gate + endpoints)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

from exlab_wizard.api import AppDependencies, create_app
from exlab_wizard.api.setup import (
    LIMSTestRequest,
    ProbeResult,
    compute_setup_state,
    is_creation_blocked,
    setup_state_gate,
)
from exlab_wizard.config.models import (
    Config,
    EquipmentConfig,
    LIMSConfig,
    PathsConfig,
    RcloneTransport,
)
from exlab_wizard.constants import SetupState


def _ready_config() -> Config:
    return Config(
        paths=PathsConfig(
            templates_dir="/tpl",
            plugin_dir="/plugin",
            local_root="/data",
        ),
        equipment=[
            EquipmentConfig(
                id="EQ1",
                label="Equipment 1",
                local_root="/data",
                nas_root="/srv/nas",
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


def test_compute_setup_state_returns_ready_for_complete_config() -> None:
    deps = AppDependencies(config=_ready_config(), lims_reachable=True)
    assert compute_setup_state(deps) is SetupState.READY


def test_compute_setup_state_no_config() -> None:
    deps = AppDependencies(config=None)
    assert compute_setup_state(deps) is SetupState.INCOMPLETE_NO_CONFIG


def test_is_creation_blocked_treats_lims_unreachable_as_soft() -> None:
    assert is_creation_blocked(SetupState.INCOMPLETE_LIMS_UNREACHABLE) is False
    assert is_creation_blocked(SetupState.READY) is False
    assert is_creation_blocked(SetupState.INCOMPLETE_NO_CONFIG) is True
    assert is_creation_blocked(SetupState.INCOMPLETE_MISSING_PATHS) is True
    assert is_creation_blocked(SetupState.INCOMPLETE_NO_EQUIPMENT) is True
    assert is_creation_blocked(SetupState.INCOMPLETE_NO_LIMS) is True


def test_setup_state_gate_returns_503_in_incomplete_states() -> None:
    """Each non-soft INCOMPLETE_* state returns a 503 with the right code."""
    test_cases = [
        (None, "incomplete_no_config"),
        (Config(), "incomplete_missing_paths"),
        (
            Config(paths=PathsConfig(templates_dir="/t", plugin_dir="/p", local_root="/d")),
            "incomplete_no_equipment",
        ),
    ]
    for config, expected_state in test_cases:
        deps = AppDependencies(config=config)
        app = FastAPI()
        app.state.dependencies = deps

        router = APIRouter()

        @router.get("/gated", dependencies=[Depends(setup_state_gate)])
        async def gated() -> dict:
            return {"ok": True}

        app.include_router(router)
        client = TestClient(app)
        response = client.get("/gated")
        assert response.status_code == 503
        body = response.json()
        # No envelope handler is registered on this bare app; the
        # handler returned the raw HTTPException detail and FastAPI
        # wraps it as ``{"detail": ...}``.
        detail = body.get("detail", body)
        assert detail["code"] == "setup_incomplete"
        assert detail["state"] == expected_state


def test_setup_state_gate_lims_unreachable_does_not_block() -> None:
    config = _ready_config()
    deps = AppDependencies(config=config, lims_reachable=False)
    app = FastAPI()
    app.state.dependencies = deps

    router = APIRouter()

    @router.get("/gated", dependencies=[Depends(setup_state_gate)])
    async def gated() -> dict:
        return {"ok": True}

    app.include_router(router)
    client = TestClient(app)
    response = client.get("/gated")
    assert response.status_code == 200


def test_setup_state_gate_no_op_without_dependencies() -> None:
    app = FastAPI()
    router = APIRouter()

    @router.get("/g", dependencies=[Depends(setup_state_gate)])
    async def g() -> dict:
        return {"ok": True}

    app.include_router(router)
    client = TestClient(app)
    assert client.get("/g").status_code == 200


def test_get_setup_status_ready() -> None:
    deps = AppDependencies(config=_ready_config(), lims_reachable=True)
    app = create_app(dependencies=deps)
    client = TestClient(app)
    response = client.get("/api/v1/setup/status")
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "ready"
    assert body["ready"] is True
    assert body["next_action"] is None


def test_get_setup_status_incomplete_paths() -> None:
    deps = AppDependencies(config=Config())
    app = create_app(dependencies=deps)
    client = TestClient(app)
    response = client.get("/api/v1/setup/status")
    body = response.json()
    assert body["state"] == "incomplete_missing_paths"
    assert body["ready"] is False
    assert body["next_action"] == "set_paths"
    field_names = {entry["field"] for entry in body["missing"]}
    assert "paths.templates_dir" in field_names


def test_post_test_lims_invokes_probe() -> None:
    async def probe(_body: LIMSTestRequest | None) -> ProbeResult:
        return ProbeResult(ok=True, latency_ms=42)

    deps = AppDependencies(config=_ready_config(), lims_probe=probe)
    app = create_app(dependencies=deps)
    client = TestClient(app)
    response = client.post("/api/v1/setup/test-lims", json={})
    assert response.status_code == 200
    assert response.json() == {"ok": True, "reason": None, "latency_ms": 42}


def test_post_test_lims_probe_raises_returns_reason() -> None:
    async def bad_probe(_body: LIMSTestRequest | None) -> ProbeResult:
        raise RuntimeError("network down")

    deps = AppDependencies(config=_ready_config(), lims_probe=bad_probe)
    app = create_app(dependencies=deps)
    client = TestClient(app)
    response = client.post("/api/v1/setup/test-lims", json={})
    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert "network down" in response.json()["reason"]


def test_post_test_lims_without_probe_reports_not_wired() -> None:
    deps = AppDependencies(config=_ready_config())
    app = create_app(dependencies=deps)
    client = TestClient(app)
    response = client.post("/api/v1/setup/test-lims", json={})
    assert response.json()["ok"] is False


def test_post_test_equipment_invokes_probe() -> None:
    captured: dict[str, Any] = {}

    def probe(equipment: Any) -> dict[str, Any]:
        captured["id"] = equipment.id
        return {"ok": True, "reason": None, "latency_ms": 10}

    deps = AppDependencies(config=_ready_config(), equipment_probe=probe)
    app = create_app(dependencies=deps)
    client = TestClient(app)
    response = client.post("/api/v1/setup/test-equipment", json={})
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert captured["id"] == "EQ1"


def test_post_test_equipment_with_explicit_equipment_id() -> None:
    captured: dict[str, Any] = {}

    def probe(equipment: Any) -> bool:
        captured["id"] = equipment.id
        return True

    deps = AppDependencies(config=_ready_config(), equipment_probe=probe)
    app = create_app(dependencies=deps)
    client = TestClient(app)
    body = {"equipment_id": "EQ1"}
    response = client.post("/api/v1/setup/test-equipment", json=body)
    assert response.json()["ok"] is True
    assert captured["id"] == "EQ1"


def test_post_test_equipment_unknown_id_returns_no_match() -> None:
    deps = AppDependencies(config=_ready_config(), equipment_probe=lambda _e: True)
    app = create_app(dependencies=deps)
    client = TestClient(app)
    response = client.post("/api/v1/setup/test-equipment", json={"equipment_id": "NO_SUCH"})
    assert response.json()["ok"] is False


def test_post_autostart_calls_toggle() -> None:
    captured = {"value": None}

    def toggle(enabled: bool) -> bool:
        captured["value"] = enabled
        return enabled

    deps = AppDependencies(config=_ready_config(), autostart_toggle=toggle)
    app = create_app(dependencies=deps)
    client = TestClient(app)
    response = client.post("/api/v1/setup/autostart", json={"enabled": True})
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is True
    assert body["registered"] is True
    assert captured["value"] is True


def test_post_autostart_without_toggle_echoes_state() -> None:
    deps = AppDependencies(config=_ready_config())
    app = create_app(dependencies=deps)
    client = TestClient(app)
    response = client.post("/api/v1/setup/autostart", json={"enabled": False})
    body = response.json()
    assert body == {"enabled": False, "registered": False}
