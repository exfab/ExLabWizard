"""Unit tests for ``exlab_wizard.api.health``."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from exlab_wizard.api import AppDependencies, create_app
from exlab_wizard.api.health import _component_rollup, _top_level_status
from exlab_wizard.config.models import (
    Config,
    EquipmentConfig,
    LIMSConfig,
    OrchestratorConfig,
    PathsConfig,
    RcloneTransport,
)


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


def test_health_endpoint_status_ok_with_no_deps() -> None:
    deps = AppDependencies(config=_ready_config())
    app = create_app(dependencies=deps)
    client = TestClient(app)
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["setup_state"] == "ready"
    assert "creation_json" in body["schema_versions"]


def test_health_endpoint_warn_when_lims_unreachable() -> None:
    deps = AppDependencies(config=_ready_config(), lims_reachable=False)
    app = create_app(dependencies=deps)
    client = TestClient(app)
    body = client.get("/api/v1/health").json()
    assert body["status"] == "warn"
    assert body["components"]["lims"]["status"] == "warn"


def test_health_components_pluck_snapshots() -> None:
    deps = AppDependencies(
        config=_ready_config(),
        nas_sync_snapshot=lambda: {"queue_depth": 3, "in_flight": 1},
        session_store_snapshot=lambda: {"active_sessions": 1, "input_required": 0},
        registered_plugin_count=8,
    )
    app = create_app(dependencies=deps)
    client = TestClient(app)
    body = client.get("/api/v1/health").json()
    components = body["components"]
    assert components["nas_sync"]["queue_depth"] == 3
    assert components["session_store"]["active_sessions"] == 1
    assert components["plugin_host"]["registered_plugins"] == 8


def test_top_level_status_aggregates() -> None:
    assert _top_level_status({"a": {"status": "ok"}}) == "ok"
    assert _top_level_status({"a": {"status": "ok"}, "b": {"status": "warn"}}) == "warn"
    assert _top_level_status({"a": {"status": "warn"}, "b": {"status": "error"}}) == "error"


def test_component_rollup_handles_none_deps() -> None:
    rollup = _component_rollup(None)
    assert set(rollup.keys()) == {"validator", "nas_sync", "lims", "plugin_host", "session_store"}


def test_health_with_validator_last_audit() -> None:
    deps = AppDependencies(config=_ready_config(), last_audit_at="2026-05-01T00:00:00Z")
    app = create_app(dependencies=deps)
    client = TestClient(app)
    body = client.get("/api/v1/health").json()
    assert body["components"]["validator"]["last_audit_at"] == "2026-05-01T00:00:00Z"


def test_health_nas_sync_snapshot_failure_marked_warn() -> None:
    def bad() -> dict[str, Any]:
        raise RuntimeError("queue closed")

    deps = AppDependencies(config=_ready_config(), nas_sync_snapshot=bad)
    app = create_app(dependencies=deps)
    client = TestClient(app)
    body = client.get("/api/v1/health").json()
    assert body["components"]["nas_sync"]["status"] == "warn"
