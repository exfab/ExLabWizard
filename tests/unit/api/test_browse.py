"""Unit tests for the ``/tree`` and ``/run/{path}`` browse endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import msgspec
from fastapi.testclient import TestClient

from exlab_wizard.api import AppDependencies, create_app
from exlab_wizard.api.schemas import (
    CreationJson,
    LimsProjectBlock,
    PathsBlock,
    TemplateBlock,
)
from exlab_wizard.config.models import (
    Config,
    EquipmentConfig,
    LIMSConfig,
    PathsConfig,
    RcloneTransport,
)
from exlab_wizard.constants import (
    CACHE_DIR_NAME,
    CREATION_JSON_NAME,
    CREATION_JSON_VERSION,
    README_FILE_NAME,
    RUN_DIR_PREFIX,
    SyncStatus,
)


def _config_with_local_root(local_root: Path) -> Config:
    return Config(
        paths=PathsConfig(
            templates_dir=str(local_root / "templates"),
            plugin_dir=str(local_root / "plugins"),
            local_root=str(local_root),
        ),
        equipment=[
            EquipmentConfig(
                id="EQ1",
                label="Equipment 1",
                local_root=str(local_root),
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


def _write_creation_json(directory: Path, *, sync_status: str = SyncStatus.PENDING.value) -> None:
    cache_dir = directory / CACHE_DIR_NAME
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = CreationJson(
        schema_version=CREATION_JSON_VERSION,
        created_at=datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        created_by="asmith",
        level="run",
        run_kind="experimental",
        lims_project=LimsProjectBlock(
            uid="x", short_id="PROJ-0042", name_at_creation="example", source="live"
        ),
        template=TemplateBlock(
            name="basic", version="1.0.0", source_path="/tpl/basic", run_scope="experimental"
        ),
        variables={},
        paths=PathsBlock(local=str(directory), nas="/srv/nas/EQ1"),
        sync_status=sync_status,
    )
    (cache_dir / CREATION_JSON_NAME).write_bytes(msgspec.json.encode(payload))


def test_get_tree_lists_equipment_and_projects(tmp_path: Path) -> None:
    local_root = tmp_path / "data"
    local_root.mkdir()
    eq_dir = local_root / "EQ1"
    # The <project>/ segment is the human-readable LIMS name, used verbatim. §3.2.
    project_dir = eq_dir / "Cortex Q3 Pilot"
    run_dir = project_dir / f"{RUN_DIR_PREFIX}2026-04-17T14-00-00"
    run_dir.mkdir(parents=True)
    _write_creation_json(run_dir)
    _write_creation_json(project_dir)

    deps = AppDependencies(config=_config_with_local_root(local_root))
    app = create_app(dependencies=deps)
    client = TestClient(app)
    response = client.get("/api/v1/tree")
    assert response.status_code == 200
    body = response.json()
    assert len(body["equipment"]) == 1
    eq = body["equipment"][0]
    assert eq["id"] == "EQ1"
    assert len(eq["projects"]) == 1
    project = eq["projects"][0]
    assert project["name"] == "Cortex Q3 Pilot"
    assert len(project["runs"]) == 1
    assert project["runs"][0]["kind"] == "experimental"
    assert project["runs"][0]["sync_status"] == SyncStatus.PENDING.value


def test_get_tree_returns_empty_when_no_equipment(tmp_path: Path) -> None:
    """When config has no equipment the setup gate blocks /tree."""
    local_root = tmp_path / "data"
    local_root.mkdir()
    config = Config(
        paths=PathsConfig(templates_dir="/t", plugin_dir="/p", local_root=str(local_root)),
    )
    deps = AppDependencies(config=config)
    app = create_app(dependencies=deps)
    client = TestClient(app)
    response = client.get("/api/v1/tree")
    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "setup_incomplete"
    assert body["error"]["state"] == "incomplete_no_equipment"


def test_get_run_returns_detail(tmp_path: Path) -> None:
    local_root = tmp_path / "data"
    local_root.mkdir()
    run_dir = local_root / "EQ1" / "PROJ-0042" / f"{RUN_DIR_PREFIX}2026-04-17T14-00-00"
    run_dir.mkdir(parents=True)
    _write_creation_json(run_dir)
    (run_dir / README_FILE_NAME).write_text("# README\n", encoding="utf-8")

    deps = AppDependencies(config=_config_with_local_root(local_root))
    app = create_app(dependencies=deps)
    client = TestClient(app)
    response = client.get(f"/api/v1/run/{run_dir}")
    assert response.status_code == 200
    body = response.json()
    assert body["operator"] == "asmith"
    assert body["template"]["name"] == "basic"
    assert body["readme"] == "# README\n"


def test_get_run_404_when_creation_missing(tmp_path: Path) -> None:
    local_root = tmp_path / "data"
    local_root.mkdir()
    deps = AppDependencies(config=_config_with_local_root(local_root))
    app = create_app(dependencies=deps)
    client = TestClient(app)
    bogus = local_root / "no_such_dir"
    response = client.get(f"/api/v1/run/{bogus}")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "session_not_found"


def test_get_tree_skips_unknown_dirs(tmp_path: Path) -> None:
    local_root = tmp_path / "data"
    eq_dir = local_root / "EQ1"
    eq_dir.mkdir(parents=True)
    project_dir = eq_dir / "PROJ-0042"
    project_dir.mkdir()
    # An unmanaged sub-folder under the project; should NOT appear in
    # ``runs`` because it does not start with ``Run_``.
    (project_dir / "scratch").mkdir()
    deps = AppDependencies(config=_config_with_local_root(local_root))
    app = create_app(dependencies=deps)
    client = TestClient(app)
    body = client.get("/api/v1/tree").json()
    project = body["equipment"][0]["projects"][0]
    assert project["runs"] == []
    assert project["test_runs"] == []


def test_get_tree_includes_test_runs(tmp_path: Path) -> None:
    """``TestRuns`` directory under a project surfaces as ``test_runs`` list."""
    from exlab_wizard.constants import TEST_RUN_DIR_PREFIX, TEST_RUNS_DIR_NAME

    local_root = tmp_path / "data"
    eq_dir = local_root / "EQ1"
    project_dir = eq_dir / "PROJ-0042"
    test_runs_marker = project_dir / TEST_RUNS_DIR_NAME
    test_run = test_runs_marker / f"{TEST_RUN_DIR_PREFIX}2026-04-17T14-00-00"
    test_run.mkdir(parents=True)
    _write_creation_json(test_run)
    deps = AppDependencies(config=_config_with_local_root(local_root))
    app = create_app(dependencies=deps)
    client = TestClient(app)
    body = client.get("/api/v1/tree").json()
    project = body["equipment"][0]["projects"][0]
    assert len(project["test_runs"]) == 1
    assert project["test_runs"][0]["kind"] == "test"


def test_get_run_returns_422_when_creation_json_malformed(tmp_path: Path) -> None:
    """A creation.json that is present but malformed surfaces as 422."""
    local_root = tmp_path / "data"
    run_dir = local_root / "EQ1" / "PROJ-0042" / "Run_2026-04-17T14-00-00"
    run_dir.mkdir(parents=True)
    cache_dir = run_dir / "_test_cache"
    cache_dir.mkdir()
    # Place a malformed creation.json at the expected cache path.
    from exlab_wizard.constants import CACHE_DIR_NAME, CREATION_JSON_NAME

    (run_dir / CACHE_DIR_NAME).mkdir(exist_ok=True)
    (run_dir / CACHE_DIR_NAME / CREATION_JSON_NAME).write_bytes(b"not json")
    deps = AppDependencies(config=_config_with_local_root(local_root))
    app = create_app(dependencies=deps)
    client = TestClient(app)
    response = client.get(f"/api/v1/run/{run_dir}")
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_failed"


def test_get_run_returns_none_readme_when_absent(tmp_path: Path) -> None:
    """Run with creation.json but no README.md returns readme: null."""
    local_root = tmp_path / "data"
    run_dir = local_root / "EQ1" / "PROJ-0042" / "Run_2026-04-17T14-00-00"
    run_dir.mkdir(parents=True)
    _write_creation_json(run_dir)
    deps = AppDependencies(config=_config_with_local_root(local_root))
    app = create_app(dependencies=deps)
    client = TestClient(app)
    response = client.get(f"/api/v1/run/{run_dir}")
    assert response.status_code == 200
    assert response.json()["readme"] is None
