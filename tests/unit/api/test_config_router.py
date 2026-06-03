"""Unit tests for the ``/config`` router."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from exlab_wizard.api import AppDependencies, create_app
from exlab_wizard.config.models import (
    Config,
    EquipmentConfig,
    NasConfig,
    OrchestratorConfig,
    PathsConfig,
)


def _empty_config() -> Config:
    return Config()


def _ready_config() -> Config:
    return Config(
        paths=PathsConfig(app_root="/srv/exlab"),
        equipment=[
            EquipmentConfig(
                id="EQ1",
                label="Equipment 1",
                nas_root="/n",
            )
        ],
        orchestrator=OrchestratorConfig(label="LAB", staging_root="/staging"),
        nas=NasConfig(remote="nas01", base_root="/srv/nas"),
    )


def test_get_config_returns_loaded_config() -> None:
    config = _ready_config()
    deps = AppDependencies(config=config)
    app = create_app(dependencies=deps)
    client = TestClient(app)
    response = client.get("/api/v1/config")
    assert response.status_code == 200
    assert response.json()["paths"]["app_root"] == "/srv/exlab"
    assert len(response.json()["equipment"]) == 1


def test_get_config_returns_default_when_none() -> None:
    deps = AppDependencies(config=None)
    app = create_app(dependencies=deps)
    client = TestClient(app)
    response = client.get("/api/v1/config")
    assert response.status_code == 200
    body = response.json()
    # The single app root defaults under the OS Documents folder, so a
    # default config no longer has an empty paths block; only ``app_root``
    # is serialized (templates/plugins/data are derived properties).
    from exlab_wizard.paths import default_app_root

    assert body["paths"] == {"app_root": str(default_app_root())}


def test_put_config_persists_and_reevaluates_state() -> None:
    captured: dict[str, Any] = {"saved": None}

    async def saver(config: Config) -> None:
        captured["saved"] = config

    # ``_ready_config`` configures ``nas.remote`` and the default deps
    # ``nas_remote_available`` predicate answers "available", so the
    # NAS-remote gate (which precedes the LIMS gate) passes and this test's
    # verdict is the LIMS gate.
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
            "nas_root": "/srv/nas",
        }
    )
    response = client.post("/api/v1/config/equipment", json=new_eq.model_dump(mode="json"))
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
            "nas_root": "/srv/nas",
        }
    )
    response = client.post("/api/v1/config/equipment", json=duplicate.model_dump(mode="json"))
    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "equipment_id_conflict"
