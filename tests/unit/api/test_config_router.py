"""Unit tests for the ``/config`` router."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from exlab_wizard.api import AppDependencies, create_app
from exlab_wizard.config.models import (
    OrchestratorConfig,
    Config,
    EquipmentConfig,
    PathsConfig,
    RcloneTransport,
)


def _empty_config() -> Config:
    return Config()


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
        orchestrator=OrchestratorConfig(label="LAB", staging_root="/staging"),
    )


def test_get_config_returns_loaded_config() -> None:
    config = _ready_config()
    deps = AppDependencies(config=config)
    app = create_app(dependencies=deps)
    client = TestClient(app)
    response = client.get("/api/v1/config")
    assert response.status_code == 200
    assert response.json()["paths"]["local_root"] == "/d"
    assert len(response.json()["equipment"]) == 1


def test_get_config_returns_default_when_none() -> None:
    deps = AppDependencies(config=None)
    app = create_app(dependencies=deps)
    client = TestClient(app)
    response = client.get("/api/v1/config")
    assert response.status_code == 200
    body = response.json()
    assert body["paths"]["local_root"] == ""


def test_put_config_persists_and_reevaluates_state() -> None:
    captured: dict[str, Any] = {"saved": None}

    async def saver(config: Config) -> None:
        captured["saved"] = config

    deps = AppDependencies(config=_empty_config(), save_config=saver)
    app = create_app(dependencies=deps)
    client = TestClient(app)
    new_config = _ready_config()
    response = client.put("/api/v1/config", json=new_config.model_dump(mode="json"))
    assert response.status_code == 200
    body = response.json()
    # Without LIMS configured the response should report no_lims.
    assert body["state"] == "incomplete_no_lims"
    assert body["ready"] is False
    # Verify the saver was called with the new model.
    assert captured["saved"] is not None
    assert captured["saved"].equipment[0].id == "EQ1"
    # And the deps now hold the new config.
    assert deps.config is not None and deps.config.equipment[0].id == "EQ1"


def test_put_config_invalid_body_returns_422() -> None:
    deps = AppDependencies(config=_empty_config())
    app = create_app(dependencies=deps)
    client = TestClient(app)
    # An unknown top-level key triggers extra=forbid.
    response = client.put("/api/v1/config", json={"unknown_field": True})
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_failed"


# ---------------------------------------------------------------------------
# POST /config/equipment (Redesign §6)
# ---------------------------------------------------------------------------


def test_append_equipment_persists_and_re_evaluates_state() -> None:
    captured: dict[str, Any] = {"saved": None}

    async def saver(config: Config) -> None:
        captured["saved"] = config

    deps = AppDependencies(config=_empty_config(), save_config=saver)
    app = create_app(dependencies=deps)
    client = TestClient(app)
    new_eq = EquipmentConfig.model_validate(
        {
            "id": "FLOW_99",
            "label": "Flow Cytometer 99",
            "local_root": "/data",
            "nas_root": "/srv/nas",
            "completeness_signal": "sentinel_file",
            "sentinel_filename": "done.flag",
            "transport": {
                "type": "rclone",
                "rclone_remote": "lab-nas",
                "rclone_remote_path": "lab/FLOW_99",
            },
        }
    )
    response = client.post(
        "/api/v1/config/equipment", json=new_eq.model_dump(mode="json")
    )
    assert response.status_code == 200
    body = response.json()
    assert body["appended_id"] == "FLOW_99"
    assert captured["saved"] is not None
    assert any(e.id == "FLOW_99" for e in deps.config.equipment)


def test_append_equipment_rejects_duplicate_id() -> None:
    deps = AppDependencies(config=_ready_config())
    app = create_app(dependencies=deps)
    client = TestClient(app)
    duplicate = EquipmentConfig.model_validate(
        {
            "id": "EQ1",
            "label": "Equipment 1 duplicate",
            "local_root": "/data",
            "nas_root": "/srv/nas",
            "completeness_signal": "sentinel_file",
            "sentinel_filename": "done.flag",
            "transport": {
                "type": "rclone",
                "rclone_remote": "lab-nas",
                "rclone_remote_path": "lab/EQ1",
            },
        }
    )
    response = client.post(
        "/api/v1/config/equipment", json=duplicate.model_dump(mode="json")
    )
    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "equipment_id_conflict"
