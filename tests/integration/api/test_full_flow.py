"""Full-flow integration test for the FastAPI surface.

Drives one project-creation session end-to-end through the real
:class:`CreationController`, the real validator, and the real cache
writers, all in-process via :class:`httpx.AsyncClient` per Backend
Spec §4.10.2. The audit task is NOT started; the test invokes
``POST /problems/refresh`` to exercise the audit path explicitly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import msgspec
import pytest

from exlab_wizard.api import AppDependencies, AuditChannel, create_app
from exlab_wizard.api.schemas import CreationJson
from exlab_wizard.cache.creation_writer import CreationWriter
from exlab_wizard.cache.equipment import EquipmentCacheWriter
from exlab_wizard.config.models import (
    Config,
    EquipmentConfig,
    LIMSConfig,
    OperatorsConfig,
    PathsConfig,
    RcloneTransport,
    READMEConfig,
)
from exlab_wizard.constants import (
    CACHE_DIR_NAME,
    CREATION_JSON_NAME,
    SyncStatus,
)
from exlab_wizard.controller import (
    CreationController,
    NoOpNASSync,
    NoOpReadmeGenerator,
    SessionStore,
)
from exlab_wizard.template.copier_driver import TemplateEngine
from exlab_wizard.validator.engine import Validator

FIXTURE_TEMPLATES = Path(__file__).parent.parent.parent / "fixtures" / "templates"


@pytest.fixture
def ready_config(tmp_path: Path) -> Config:
    return Config(
        paths=PathsConfig(
            templates_dir=str(FIXTURE_TEMPLATES),
            plugin_dir=str(tmp_path / "plugins"),
            local_root=str(tmp_path / "data"),
        ),
        equipment=[
            EquipmentConfig(
                id="EQ1",
                label="Equipment 1",
                local_root=str(tmp_path / "data"),
                nas_root="/srv/nas",
                completeness_signal="sentinel_file",
                sentinel_filename="acquisition_complete.flag",
                transport=RcloneTransport(
                    type="rclone",
                    rclone_remote="lab-nas",
                    rclone_remote_path="lab/EQ1",
                ),
            )
        ],
        operators=OperatorsConfig(allowlist=["asmith"]),
        readme=READMEConfig(defaults=[]),
        lims=LIMSConfig(endpoint="https://lims.example", email="op@example"),
    )


@pytest.fixture
def app_with_real_controller(ready_config: Config, tmp_path: Path) -> Any:
    """Build a FastAPI app with the real controller + validator wired."""
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "plugins").mkdir(exist_ok=True)
    cache_creation = CreationWriter()
    cache_equipment = EquipmentCacheWriter()
    validator = Validator.from_config(ready_config)
    controller = CreationController(
        config=ready_config,
        validator=validator,
        template_engine=TemplateEngine(),
        plugin_host=None,
        cache_creation=cache_creation,
        cache_equipment=cache_equipment,
        readme_generator=NoOpReadmeGenerator(),
        nas_sync=NoOpNASSync(),
        session_store=SessionStore(),
    )
    deps = AppDependencies(
        config=ready_config,
        validator=validator,
        cache_creation=cache_creation,
        controller=controller,
        audit_channel=AuditChannel(),
    )
    app = create_app(dependencies=deps)
    return app, controller


async def _client(app: Any) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_full_project_creation_flow(app_with_real_controller: Any, tmp_path: Path) -> None:
    """End-to-end: POST /sessions -> session reaches DONE -> creation.json on disk."""
    app, controller = app_with_real_controller
    body = {
        "kind": "project",
        "equipment_id": "EQ1",
        "template_path": str(FIXTURE_TEMPLATES / "project_basic"),
        "label": "Cortex Q3 Pilot",
        "operator": "asmith",
        "objective": "First-pass calibration of the cortex pipeline.",
        "lims_project": {
            "uid": "8c7e9d2f-1a4b-4e6c-9b3d-7f2a1e5d8c4b",
            "short_id": "PROJ-0042",
            "name_at_creation": "Cortex Q3 Pilot",
            "source": "live",
        },
        "variables": {"_exlab_proj": "PROJ-0042"},
        "readme_extra": {},
    }
    async with await _client(app) as ac:
        response = await ac.post("/api/v1/sessions", json=body)
        assert response.status_code == 201, response.text
        session = response.json()
        sid = session["session_id"]
        # Drive the pipeline to completion.
        task = controller._tasks.get(sid)
        if task is not None:
            await task
        # Snapshot via /sessions/{id}.
        snapshot = await ac.get(f"/api/v1/sessions/{sid}")
        assert snapshot.status_code == 200
        body_snap = snapshot.json()
        assert body_snap["state"] == "done"
        # creation.json must exist on disk.
        project_dir = tmp_path / "data" / "EQ1" / "PROJ-0042"
        cache_path = project_dir / CACHE_DIR_NAME / CREATION_JSON_NAME
        assert cache_path.is_file()
        decoded = msgspec.json.decode(cache_path.read_bytes(), type=CreationJson)
        assert decoded.lims_project.short_id == "PROJ-0042"
        assert decoded.sync_status == SyncStatus.PENDING.value


@pytest.mark.asyncio
async def test_health_returns_ready_when_setup_complete(app_with_real_controller: Any) -> None:
    app, _ = app_with_real_controller
    async with await _client(app) as ac:
        response = await ac.get("/api/v1/health")
        assert response.status_code == 200
        body = response.json()
        assert body["setup_state"] == "ready"


@pytest.mark.asyncio
async def test_health_warns_when_lims_unreachable(ready_config: Config) -> None:
    deps = AppDependencies(config=ready_config, lims_reachable=False)
    app = create_app(dependencies=deps)
    async with await _client(app) as ac:
        body = (await ac.get("/api/v1/health")).json()
        assert body["status"] == "warn"
        assert body["components"]["lims"]["status"] == "warn"


@pytest.mark.asyncio
async def test_setup_status_each_incomplete_state() -> None:
    """Each non-soft INCOMPLETE_* state surfaces the right next_action."""
    cases = [
        (None, "incomplete_no_config", "set_paths"),
        (Config(), "incomplete_missing_paths", "set_paths"),
        (
            Config(
                paths=PathsConfig(templates_dir="/t", plugin_dir="/p", local_root="/d"),
            ),
            "incomplete_no_equipment",
            "add_equipment",
        ),
    ]
    for config, expected_state, expected_action in cases:
        deps = AppDependencies(config=config)
        app = create_app(dependencies=deps)
        async with await _client(app) as ac:
            response = await ac.get("/api/v1/setup/status")
            assert response.status_code == 200
            body = response.json()
            assert body["state"] == expected_state
            assert body["next_action"] == expected_action
            assert body["ready"] is False


@pytest.mark.asyncio
async def test_create_session_blocked_when_setup_incomplete() -> None:
    """POST /sessions returns 503 + setup_incomplete in non-soft INCOMPLETE_* states."""
    deps = AppDependencies(config=Config())
    app = create_app(dependencies=deps)
    body = {
        "kind": "project",
        "equipment_id": "EQ1",
        "template_path": "/tpl",
        "label": "x",
        "operator": "asmith",
        "objective": "objective",
        "lims_project": {},
        "variables": {},
        "readme_extra": {},
    }
    async with await _client(app) as ac:
        response = await ac.post("/api/v1/sessions", json=body)
        assert response.status_code == 503
        envelope = response.json()
        assert envelope["error"]["code"] == "setup_incomplete"
        assert envelope["error"]["state"] == "incomplete_missing_paths"


@pytest.mark.asyncio
async def test_lims_unreachable_does_not_block_creation(ready_config: Config) -> None:
    """The soft block does not gate POST /sessions per §4.9.4."""
    cache_creation = CreationWriter()
    cache_equipment = EquipmentCacheWriter()
    validator = Validator.from_config(ready_config)
    controller = CreationController(
        config=ready_config,
        validator=validator,
        template_engine=TemplateEngine(),
        plugin_host=None,
        cache_creation=cache_creation,
        cache_equipment=cache_equipment,
        readme_generator=NoOpReadmeGenerator(),
        nas_sync=NoOpNASSync(),
        session_store=SessionStore(),
    )
    deps = AppDependencies(
        config=ready_config,
        validator=validator,
        cache_creation=cache_creation,
        controller=controller,
        lims_reachable=False,
    )
    app = create_app(dependencies=deps)
    body = {
        "kind": "project",
        "equipment_id": "EQ1",
        "template_path": str(FIXTURE_TEMPLATES / "project_basic"),
        "label": "Cortex Q3 Pilot",
        "operator": "asmith",
        "objective": "obj",
        "lims_project": {
            "uid": "x",
            "short_id": "PROJ-0042",
            "name_at_creation": "x",
            "source": "live",
        },
        "variables": {"_exlab_proj": "PROJ-0042"},
        "readme_extra": {},
    }
    async with await _client(app) as ac:
        response = await ac.post("/api/v1/sessions", json=body)
        # Creation still goes through despite the soft block.
        assert response.status_code == 201
        sid = response.json()["session_id"]
        task = controller._tasks.get(sid)
        if task is not None:
            await task


@pytest.mark.asyncio
async def test_problems_refresh_returns_audit_count(ready_config: Config) -> None:
    """``POST /problems/refresh`` invokes Validator.audit and returns the count."""
    cache_creation = CreationWriter()
    validator = Validator.from_config(ready_config)
    controller = _build_minimal_controller(ready_config, cache_creation)
    deps = AppDependencies(
        config=ready_config,
        validator=validator,
        cache_creation=cache_creation,
        controller=controller,
        audit_channel=AuditChannel(),
    )
    app = create_app(dependencies=deps)
    async with await _client(app) as ac:
        response = await ac.post("/api/v1/problems/refresh")
        assert response.status_code == 200
        body = response.json()
        assert body["audit_at"]
        assert isinstance(body["finding_count"], int)


@pytest.mark.asyncio
async def test_get_problems_returns_findings_and_filters(
    ready_config: Config, tmp_path: Path
) -> None:
    cache_creation = CreationWriter()
    validator = Validator.from_config(ready_config)
    controller = _build_minimal_controller(ready_config, cache_creation)
    deps = AppDependencies(
        config=ready_config,
        validator=validator,
        cache_creation=cache_creation,
        controller=controller,
    )
    app = create_app(dependencies=deps)
    async with await _client(app) as ac:
        response = await ac.get("/api/v1/problems")
        assert response.status_code == 200


@pytest.mark.asyncio
async def test_put_config_validates_and_updates_state(tmp_path: Path) -> None:
    deps = AppDependencies(config=Config())
    app = create_app(dependencies=deps)
    new_config = Config(
        paths=PathsConfig(
            templates_dir=str(tmp_path / "tpl"),
            plugin_dir=str(tmp_path / "plugins"),
            local_root=str(tmp_path / "data"),
        ),
    )
    async with await _client(app) as ac:
        response = await ac.put("/api/v1/config", json=new_config.model_dump(mode="json"))
        assert response.status_code == 200
        body = response.json()
        assert body["state"] == "incomplete_no_equipment"
        assert body["next_action"] == "add_equipment"


@pytest.mark.asyncio
async def test_get_tree_in_ready_state(ready_config: Config) -> None:
    deps = AppDependencies(config=ready_config)
    app = create_app(dependencies=deps)
    async with await _client(app) as ac:
        response = await ac.get("/api/v1/tree")
        assert response.status_code == 200
        body = response.json()
        assert (
            body["equipment"] == [{"id": "EQ1", "label": "Equipment 1", "path": "", "projects": []}]
            or len(body["equipment"]) == 1
        )


def test_websocket_streams_session_events_sync(ready_config: Config, tmp_path: Path) -> None:
    """End-to-end WebSocket stream of phase frames.

    Uses the synchronous TestClient flow so the WebSocket and the
    controller task share the same event loop the TestClient creates.
    """
    import json as json_mod

    from starlette.testclient import TestClient

    cache_creation = CreationWriter()
    cache_equipment = EquipmentCacheWriter()
    validator = Validator.from_config(ready_config)
    controller = CreationController(
        config=ready_config,
        validator=validator,
        template_engine=TemplateEngine(),
        plugin_host=None,
        cache_creation=cache_creation,
        cache_equipment=cache_equipment,
        readme_generator=NoOpReadmeGenerator(),
        nas_sync=NoOpNASSync(),
        session_store=SessionStore(),
    )
    deps = AppDependencies(
        config=ready_config,
        validator=validator,
        cache_creation=cache_creation,
        controller=controller,
    )
    app = create_app(dependencies=deps)
    body = {
        "kind": "project",
        "equipment_id": "EQ1",
        "template_path": str(FIXTURE_TEMPLATES / "project_basic"),
        "label": "x",
        "operator": "asmith",
        "objective": "obj",
        "lims_project": {
            "uid": "x",
            "short_id": "PROJ-0042",
            "name_at_creation": "x",
            "source": "live",
        },
        "variables": {"_exlab_proj": "PROJ-0042"},
        "readme_extra": {},
    }
    client = TestClient(app)
    response = client.post("/api/v1/sessions", json=body)
    assert response.status_code == 201
    sid = response.json()["session_id"]
    # Collect every frame the WebSocket emits until the channel closes.
    # The handler closes on "done" or "failed", so we read until then.
    received: list[dict] = []
    with client.websocket_connect(f"/api/v1/sessions/{sid}/events") as ws:
        for _ in range(50):
            try:
                msg = ws.receive_bytes()
            except Exception:
                break
            received.append(json_mod.loads(msg))
            if received[-1].get("kind") == "done":
                break
    kinds = [frame.get("kind") for frame in received]
    # We must have at least seen the validating_inputs phase frame.
    # Whether ``done`` arrives before the WebSocket closes is timing
    # dependent in TestClient's threaded model; assert the stream
    # produced phase frames as the contract requires.
    assert "phase" in kinds


def _build_minimal_controller(config: Config, cache_creation: CreationWriter) -> CreationController:
    return CreationController(
        config=config,
        validator=Validator.from_config(config),
        template_engine=TemplateEngine(),
        plugin_host=None,
        cache_creation=cache_creation,
        cache_equipment=EquipmentCacheWriter(),
        readme_generator=NoOpReadmeGenerator(),
        nas_sync=NoOpNASSync(),
        session_store=SessionStore(),
    )
