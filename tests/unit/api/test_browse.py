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
    OrchestratorConfig,
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
        orchestrator=OrchestratorConfig(label="LAB", staging_root="/staging"),
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
        orchestrator=OrchestratorConfig(label="LAB", staging_root="/staging"),
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


# ---------------------------------------------------------------------------
# GET /folder (Redesign §5)
# ---------------------------------------------------------------------------


def test_get_folder_returns_immediate_contents(tmp_path: Path) -> None:
    local_root = tmp_path / "data"
    folder = local_root / "EQ1" / "PROJ-0042" / "Runs" / "Run_2026-04-17T14-32"
    folder.mkdir(parents=True)
    (folder / "scan.tif").write_bytes(b"\x00" * 1024)
    (folder / "metadata.json").write_text('{"k": "v"}', encoding="utf-8")
    (folder / "subdir").mkdir()
    deps = AppDependencies(config=_config_with_local_root(local_root))
    app = create_app(dependencies=deps)
    client = TestClient(app)
    response = client.get(f"/api/v1/folder/{folder}")
    assert response.status_code == 200
    body = response.json()
    names = {row["name"] for row in body["entries"]}
    assert names == {"scan.tif", "metadata.json", "subdir"}
    subdir_row = next(row for row in body["entries"] if row["name"] == "subdir")
    assert subdir_row["is_dir"] is True
    scan_row = next(row for row in body["entries"] if row["name"] == "scan.tif")
    assert scan_row["size_bytes"] == 1024


def test_get_folder_404_on_vanished_path(tmp_path: Path) -> None:
    deps = AppDependencies(config=_config_with_local_root(tmp_path / "data"))
    app = create_app(dependencies=deps)
    client = TestClient(app)
    response = client.get(f"/api/v1/folder/{tmp_path / 'does-not-exist'}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "folder_not_found"


def test_get_tree_includes_sync_mode_on_equipment(tmp_path: Path) -> None:
    local_root = tmp_path / "data"
    local_root.mkdir()
    deps = AppDependencies(config=_config_with_local_root(local_root))
    app = create_app(dependencies=deps)
    client = TestClient(app)
    body = client.get("/api/v1/tree").json()
    assert body["equipment"][0]["sync_mode"] == "nas"
    assert body["equipment"][0]["relay"] is False
    assert body["received_equipment"] == []


def test_get_folder_rejects_path_outside_configured_roots(tmp_path: Path) -> None:
    """Path-confinement guard: only the configured local_root /
    staging_root / templates / plugins are listable via GET /folder."""
    local_root = tmp_path / "data"
    local_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("nope")
    deps = AppDependencies(config=_config_with_local_root(local_root))
    app = create_app(dependencies=deps)
    client = TestClient(app)
    response = client.get(f"/api/v1/folder/{outside}")
    assert response.status_code == 403
    body = response.json()
    assert body["error"]["code"] == "permission_denied"


# ---------------------------------------------------------------------------
# GET /run/{path}/log -- Redesign §4.6 View-log surface
# ---------------------------------------------------------------------------


def _write_ingest_json(
    run_dir: Path,
    *,
    current_state: str,
    history: list[dict],
) -> None:
    from exlab_wizard.api.schemas import IngestJson
    from exlab_wizard.cache.ingest_writer import IngestWriter
    from exlab_wizard.constants import INGEST_JSON_NAME, INGEST_JSON_VERSION

    cache = run_dir / CACHE_DIR_NAME
    cache.mkdir(parents=True, exist_ok=True)
    payload = msgspec.convert(
        {
            "schema_version": INGEST_JSON_VERSION,
            "project_name": "PROJ-0001",
            "equipment_id": "EQ1",
            "run_kind": "experimental",
            "run_path": str(run_dir),
            "transport": "smb_mount",
            "current_state": current_state,
            "history": history,
        },
        type=IngestJson,
    )
    # Use synchronous write since the IngestWriter's async API requires
    # an event loop here; msgspec encoded payload + write_bytes is fine
    # for test fixtures.
    _ = IngestWriter  # keep import for type/intent clarity
    (cache / INGEST_JSON_NAME).write_bytes(msgspec.json.encode(payload))


def test_get_run_log_returns_history_entries(tmp_path: Path) -> None:
    """A staged run's ingest.json history is surfaced as the log."""
    from exlab_wizard.constants import IngestState

    run_dir = tmp_path / "data" / "EQ1" / "PROJ-0001" / "Run_2026-05-01T10-00-00"
    run_dir.mkdir(parents=True)
    _write_ingest_json(
        run_dir,
        current_state=IngestState.SYNC_VERIFIED.value,
        history=[
            {
                "state": IngestState.STAGING.value,
                "at": "2026-05-01T10:00:00Z",
                "host": "h1",
            },
            {
                "state": IngestState.COMPLETE.value,
                "at": "2026-05-01T10:30:00Z",
                "host": "h1",
                "files_received": 12,
            },
            {
                "state": IngestState.SYNC_QUEUED.value,
                "at": "2026-05-01T10:31:00Z",
                "host": "h1",
            },
            {
                "state": IngestState.SYNC_VERIFIED.value,
                "at": "2026-05-01T10:35:00Z",
                "host": "h1",
            },
        ],
    )
    deps = AppDependencies(config=_config_with_local_root(tmp_path / "data"))
    app = create_app(dependencies=deps)
    client = TestClient(app)
    resp = client.get(f"/api/v1/run/{run_dir}/log")
    assert resp.status_code == 200
    body = resp.json()
    assert body["path"] == str(run_dir)
    assert body["current_state"] == IngestState.SYNC_VERIFIED.value
    assert len(body["history"]) == 4
    states = [entry["state"] for entry in body["history"]]
    assert states == [
        IngestState.STAGING.value,
        IngestState.COMPLETE.value,
        IngestState.SYNC_QUEUED.value,
        IngestState.SYNC_VERIFIED.value,
    ]
    # Extra ingest fields (e.g. files_received) come through as payload.
    assert body["history"][1]["payload"] == {"files_received": 12}


def test_get_run_log_404_when_ingest_missing(tmp_path: Path) -> None:
    """A run without an ingest.json returns 404 ``ingest_not_found``."""
    deps = AppDependencies(config=_config_with_local_root(tmp_path))
    app = create_app(dependencies=deps)
    client = TestClient(app)
    resp = client.get(f"/api/v1/run/{tmp_path}/nope/log")
    assert resp.status_code == 404
    # Reuses the existing ``session_not_found`` code -- same allowlist as
    # the run-detail endpoint (creation.json missing vs ingest.json
    # missing share semantics: the run record is unreadable).
    assert resp.json()["error"]["code"] == "session_not_found"


def test_get_run_log_422_when_ingest_malformed(tmp_path: Path) -> None:
    """A corrupt ingest.json surfaces 422 from msgspec."""
    run_dir = tmp_path / "data" / "EQ1" / "PROJ-0001" / "Run_x"
    cache = run_dir / CACHE_DIR_NAME
    cache.mkdir(parents=True)
    from exlab_wizard.constants import INGEST_JSON_NAME

    (cache / INGEST_JSON_NAME).write_bytes(b"{not-valid-json")
    deps = AppDependencies(config=_config_with_local_root(tmp_path / "data"))
    app = create_app(dependencies=deps)
    client = TestClient(app)
    resp = client.get(f"/api/v1/run/{run_dir}/log")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# build_hierarchy_dict / scan_folder_sync -- consumed by NiceGUI mount
# ---------------------------------------------------------------------------


def test_build_hierarchy_dict_returns_empty_when_config_missing() -> None:
    from exlab_wizard.api.routers import browse

    assert browse.build_hierarchy_dict(None) == {}


def test_build_hierarchy_dict_includes_owned_and_relay_equipment(tmp_path: Path) -> None:
    """The nested dict surfaces both owned and received-equipment roots
    with their relay flag set appropriately."""
    from exlab_wizard.api.routers import browse
    from exlab_wizard.ui.components import tree as ui_tree

    local_root = tmp_path / "data"
    eq1_dir = local_root / "EQ1"
    eq1_dir.mkdir(parents=True)
    # Project under owned equipment so the helper has something to nest.
    (eq1_dir / "PROJ-0001").mkdir()

    # A relay equipment with one project, surfaced via the staging root.
    # build_received_equipment_nodes only emits a relay node when the
    # equipment dir has at least one project subdirectory (an empty
    # relay dir is skipped).
    relay_root = tmp_path / "staging"
    relay_dir = relay_root / "RELAY_EQX" / "PROJ-Relay"
    relay_dir.mkdir(parents=True)

    config = _config_with_local_root(local_root)
    # Re-point staging_root onto our seeded relay tree so
    # build_received_equipment_nodes finds RELAY_EQX.
    config.orchestrator.staging_root = str(relay_root)

    hierarchy = browse.build_hierarchy_dict(config)
    # Two equipment roots present (one owned, one relay).
    eq_ids = {(k.equipment_id, k.relay) for k in hierarchy}
    assert ("EQ1", False) in eq_ids
    assert ("RELAY_EQX", True) in eq_ids
    # Keys are the ui_tree dataclass instances (not the API EquipmentNode).
    for key in hierarchy:
        assert isinstance(key, ui_tree.EquipmentNode)


def test_scan_folder_sync_lists_immediate_contents(tmp_path: Path) -> None:
    """``scan_folder_sync`` shares the same shape as the route handler."""
    from exlab_wizard.api.routers import browse

    local_root = tmp_path / "data"
    sub = local_root / "EQ1"
    sub.mkdir(parents=True)
    (sub / "file.txt").write_text("hello")
    (sub / "child").mkdir()
    config = _config_with_local_root(local_root)
    resp = browse.scan_folder_sync(str(sub), config)
    names = {entry.name for entry in resp.entries}
    assert names == {"file.txt", "child"}
    by_name = {entry.name: entry for entry in resp.entries}
    assert by_name["file.txt"].is_dir is False
    assert by_name["child"].is_dir is True
    assert resp.path == str(sub.resolve())


def test_scan_folder_sync_raises_404_for_missing_path(tmp_path: Path) -> None:
    """A missing folder raises the same 404 HTTPException the route does."""
    from fastapi import HTTPException

    from exlab_wizard.api.routers import browse

    config = _config_with_local_root(tmp_path)
    try:
        browse.scan_folder_sync(str(tmp_path / "nope"), config)
    except HTTPException as exc:
        assert exc.status_code == 404
    else:  # pragma: no cover -- defensive
        raise AssertionError("expected HTTPException")
