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
    NasConfig,
    OrchestratorConfig,
    PathsConfig,
)
from exlab_wizard.constants import (
    CACHE_DIR_NAME,
    CREATION_JSON_NAME,
    CREATION_JSON_VERSION,
    README_FILE_NAME,
    RUN_DIR_PREFIX,
    RunSyncState,
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
            )
        ],
        lims=LIMSConfig(endpoint="https://lims.example", email="op@example"),
        orchestrator=OrchestratorConfig(label="LAB", staging_root="/staging"),
        nas=NasConfig(remote="nas01", base_root="/srv/nas"),
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
    # Operator-free per-file NAS sync design (2026-05-21): the run-node
    # rollup is derived from sync_state.json. A run with no sync_state.json
    # yet rolls up to ``syncing`` (nothing tracked / verified).
    assert project["runs"][0]["sync_status"] == RunSyncState.SYNCING.value


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
    deps = AppDependencies(
        config=_config_with_local_root(tmp_path / "data")
    )
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


class _StubJobRow:
    """A minimal sync-queue job row for the run-log tests."""

    def __init__(self, *, run_path: str, state: str) -> None:
        from exlab_wizard.sync.queue import SyncJobState

        self.run_path = run_path
        self.state = SyncJobState(state)
        self.attempts = 2
        self.verify_passes = 1
        self.last_error: str | None = None
        self.nas_path: str | None = None
        self.verified_at = "2026-05-01T10:35:00Z"
        self.enqueued_at = "2026-05-01T10:00:00Z"


class _StubNasSync:
    """Sync-queue stub serving ``get_by_run_path`` for the log endpoint."""

    def __init__(self, row: _StubJobRow | None = None) -> None:
        self._row = row

    async def get_by_run_path(self, run_path: Path) -> _StubJobRow | None:
        if self._row is None or self._row.run_path != str(run_path):
            return None
        return self._row


def test_get_run_log_returns_queue_derived_history(tmp_path: Path) -> None:
    """A staged run's sync-queue job state is surfaced as the log."""
    run_dir = tmp_path / "data" / "EQ1" / "PROJ-0001" / "Run_2026-05-01T10-00-00"
    run_dir.mkdir(parents=True)
    nas_sync = _StubNasSync(_StubJobRow(run_path=str(run_dir), state="verified"))
    deps = AppDependencies(
        config=_config_with_local_root(tmp_path / "data"),
        nas_sync=nas_sync,
    )
    app = create_app(dependencies=deps)
    client = TestClient(app)
    resp = client.get(f"/api/v1/run/{run_dir}/log")
    assert resp.status_code == 200
    body = resp.json()
    assert body["path"] == str(run_dir)
    assert body["current_state"] == "verified"
    assert len(body["history"]) == 1
    assert body["history"][0]["state"] == "verified"
    # Queue extras (attempts, verify_passes) come through as payload.
    assert body["history"][0]["payload"]["attempts"] == 2


def test_get_run_log_forwards_failure_extras_in_payload(tmp_path: Path) -> None:
    """A failed job row's ``last_error`` / ``attempts`` / ``verify_passes`` /
    ``nas_path`` are all forwarded into the log entry's free-form payload.

    Directly exercises the ``extras`` extraction in
    :func:`browse._run_log_from_queue` -- the baseline test leaves
    ``last_error`` / ``nas_path`` unset, so this asserts the truthy-only
    extraction picks up every populated extra.
    """
    run_dir = tmp_path / "data" / "EQ1" / "PROJ-0001" / "Run_2026-05-02T08-00-00"
    run_dir.mkdir(parents=True)
    row = _StubJobRow(run_path=str(run_dir), state="failed")
    row.attempts = 4
    row.verify_passes = 0
    row.last_error = "transport timeout"
    row.nas_path = "/srv/nas/EQ1/run"
    deps = AppDependencies(
        config=_config_with_local_root(tmp_path / "data"),
        nas_sync=_StubNasSync(row),
    )
    app = create_app(dependencies=deps)
    client = TestClient(app)
    resp = client.get(f"/api/v1/run/{run_dir}/log")
    assert resp.status_code == 200
    payload = resp.json()["history"][0]["payload"]
    assert payload["attempts"] == 4
    assert payload["last_error"] == "transport timeout"
    assert payload["nas_path"] == "/srv/nas/EQ1/run"
    # ``verify_passes`` is 0 (falsy) -- the truthy-only extraction omits it.
    assert "verify_passes" not in payload


def test_get_run_log_empty_history_when_no_queue_job(tmp_path: Path) -> None:
    """A run with no sync-queue job returns an empty history + 'none' state."""
    run_dir = tmp_path / "data" / "EQ1" / "PROJ-0001" / "Run_x"
    run_dir.mkdir(parents=True)
    deps = AppDependencies(
        config=_config_with_local_root(tmp_path / "data"),
        nas_sync=_StubNasSync(),
    )
    app = create_app(dependencies=deps)
    client = TestClient(app)
    resp = client.get(f"/api/v1/run/{run_dir}/log")
    assert resp.status_code == 200
    body = resp.json()
    assert body["current_state"] == "none"
    assert body["history"] == []


def test_get_run_log_404_when_run_missing(tmp_path: Path) -> None:
    """A run directory that does not exist returns 404 ``session_not_found``."""
    deps = AppDependencies(config=_config_with_local_root(tmp_path))
    app = create_app(dependencies=deps)
    client = TestClient(app)
    resp = client.get(f"/api/v1/run/{tmp_path}/nope/log")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "session_not_found"


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


# ---------------------------------------------------------------------------
# Per-file sync status from sync_state.json + cleared-run tombstones
# (operator-free per-file NAS sync design, 2026-05-21)
# ---------------------------------------------------------------------------


def _seed_run_with_creation(local_root: Path) -> Path:
    """Create a run dir with a creation.json so it is recognised as a run."""
    eq_dir = local_root / "EQ1"
    run_dir = eq_dir / "Cortex" / f"{RUN_DIR_PREFIX}2026-05-21T00-00-00"
    run_dir.mkdir(parents=True)
    _write_creation_json(run_dir)
    return run_dir


def _write_sync_state(run_dir: Path, files: dict, *, cleared: bool = False) -> None:
    """Write a sync_state.json with the given per-file records."""
    import asyncio

    from exlab_wizard.cache.sync_state_writer import SyncStateWriter

    writer = SyncStateWriter()
    for rel, rec in files.items():
        asyncio.run(
            writer.upsert_file(
                run_dir,
                rel,
                synced_signature=rec.get("synced_signature"),
                verified_at=rec.get("verified_at"),
            )
        )
        if rec.get("keep_local"):
            asyncio.run(writer.set_keep_local(run_dir, rel, True))
    if cleared:
        asyncio.run(writer.mark_cleared(run_dir))


def test_per_file_status_acquiring_when_not_in_sync_state(tmp_path: Path) -> None:
    """A file on disk but absent from sync_state.json reads as ``acquiring``."""
    from exlab_wizard.api.routers import browse

    local_root = tmp_path / "data"
    run_dir = _seed_run_with_creation(local_root)
    (run_dir / "scan.tif").write_bytes(b"x" * 10)
    config = _config_with_local_root(local_root)
    resp = browse.scan_folder_sync(str(run_dir), config)
    by_name = {e.name: e for e in resp.entries}
    assert by_name["scan.tif"].sync_status == "acquiring"


def test_per_file_status_syncing_when_unverified(tmp_path: Path) -> None:
    """A recorded file with verified_at null + on disk reads as ``syncing``."""
    from exlab_wizard.api.routers import browse

    local_root = tmp_path / "data"
    run_dir = _seed_run_with_creation(local_root)
    (run_dir / "scan.tif").write_bytes(b"x" * 10)
    _write_sync_state(run_dir, {"scan.tif": {"synced_signature": (10, 1)}})
    config = _config_with_local_root(local_root)
    resp = browse.scan_folder_sync(str(run_dir), config)
    by_name = {e.name: e for e in resp.entries}
    assert by_name["scan.tif"].sync_status == "syncing"


def test_per_file_status_synced_when_verified_and_on_disk(tmp_path: Path) -> None:
    """A recorded+verified file still on disk reads as ``synced``."""
    from exlab_wizard.api.routers import browse

    local_root = tmp_path / "data"
    run_dir = _seed_run_with_creation(local_root)
    (run_dir / "scan.tif").write_bytes(b"x" * 10)
    _write_sync_state(
        run_dir,
        {"scan.tif": {"synced_signature": (10, 1), "verified_at": "2026-05-21T01:00:00Z"}},
    )
    config = _config_with_local_root(local_root)
    resp = browse.scan_folder_sync(str(run_dir), config)
    by_name = {e.name: e for e in resp.entries}
    assert by_name["scan.tif"].sync_status == "synced"


def test_per_file_status_on_nas_tombstone_for_cleared_run(tmp_path: Path) -> None:
    """A cleared run lists verified-but-absent files as ``on_nas`` tombstones."""
    from exlab_wizard.api.routers import browse

    local_root = tmp_path / "data"
    run_dir = _seed_run_with_creation(local_root)
    # File verified, then cleared from disk -- only sync_state.json remains.
    _write_sync_state(
        run_dir,
        {"scan.tif": {"synced_signature": (10, 1), "verified_at": "2026-05-21T01:00:00Z"}},
        cleared=True,
    )
    config = _config_with_local_root(local_root)
    resp = browse.scan_folder_sync(str(run_dir), config)
    by_name = {e.name: e for e in resp.entries}
    assert "scan.tif" in by_name
    tomb = by_name["scan.tif"]
    assert tomb.sync_status == "on_nas"
    assert tomb.tombstone is True
    assert tomb.is_dir is False
    assert tomb.size_bytes is None


def test_per_file_status_keep_local_flag_carried(tmp_path: Path) -> None:
    """A keep_local file carries the flag alongside its sync status."""
    from exlab_wizard.api.routers import browse

    local_root = tmp_path / "data"
    run_dir = _seed_run_with_creation(local_root)
    (run_dir / "scan.tif").write_bytes(b"x" * 10)
    _write_sync_state(
        run_dir,
        {
            "scan.tif": {
                "synced_signature": (10, 1),
                "verified_at": "2026-05-21T01:00:00Z",
                "keep_local": True,
            }
        },
    )
    config = _config_with_local_root(local_root)
    resp = browse.scan_folder_sync(str(run_dir), config)
    by_name = {e.name: e for e in resp.entries}
    assert by_name["scan.tif"].sync_status == "synced"
    assert by_name["scan.tif"].keep_local is True


def test_build_run_node_rollup_from_sync_state(tmp_path: Path) -> None:
    """The tree run-node sync_status is the sync_state.json rollup, not creation.json."""
    local_root = tmp_path / "data"
    run_dir = _seed_run_with_creation(local_root)
    _write_sync_state(
        run_dir,
        {"scan.tif": {"synced_signature": (10, 1), "verified_at": "2026-05-21T01:00:00Z"}},
    )
    deps = AppDependencies(config=_config_with_local_root(local_root))
    app = create_app(dependencies=deps)
    client = TestClient(app)
    body = client.get("/api/v1/tree").json()
    run = body["equipment"][0]["projects"][0]["runs"][0]
    assert run["sync_status"] == RunSyncState.SYNCED.value


def test_build_run_node_rollup_cleared(tmp_path: Path) -> None:
    """A cleared run rolls up to ``cleared`` in the tree."""
    local_root = tmp_path / "data"
    run_dir = _seed_run_with_creation(local_root)
    _write_sync_state(
        run_dir,
        {"scan.tif": {"synced_signature": (10, 1), "verified_at": "2026-05-21T01:00:00Z"}},
        cleared=True,
    )
    deps = AppDependencies(config=_config_with_local_root(local_root))
    app = create_app(dependencies=deps)
    client = TestClient(app)
    body = client.get("/api/v1/tree").json()
    run = body["equipment"][0]["projects"][0]["runs"][0]
    assert run["sync_status"] == RunSyncState.CLEARED.value
