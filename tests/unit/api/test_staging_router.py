"""Unit tests for the ``/staging`` router. Backend Spec §13.7, §13.8.

The operator-free per-file NAS sync redesign (2026-05-21) removed
``ingest.json``; Phase 5 derives a run's ``current_state`` from the
``sync_state.json`` rollup (``syncing`` / ``synced`` / ``cleared``), and
the clearable set is the fully-``synced`` rollup.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from fastapi.testclient import TestClient

from exlab_wizard.api import AppDependencies, create_app
from exlab_wizard.cache.sync_state_writer import SyncStateWriter
from exlab_wizard.config.models import (
    Config,
    EquipmentConfig,
    OrchestratorConfig,
    OrchestratorStagingCleanup,
    PathsConfig,
    RcloneSftpTransport,
)
from exlab_wizard.constants import CACHE_DIR_NAME, RUNS_DIR_NAME

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class _Handle:
    job_id: str = "job-123"
    state: str = "queued"
    run_path: str = ""


class _StubNasSync:
    """In-memory sync-queue stub recording ``enqueue`` calls (force-sync)."""

    def __init__(self) -> None:
        self.enqueued: list[Path] = []

    async def enqueue(self, run_path: Path) -> _Handle:
        self.enqueued.append(run_path)
        return _Handle(run_path=str(run_path))


def _make_config(staging_root: Path, *, enabled: bool = True) -> Config:
    return Config(
        paths=PathsConfig(local_root=str(staging_root)),
        equipment=[
            EquipmentConfig(
                id="EQ1",
                label="Equipment 1",
                local_root=str(staging_root),
                nas_root="/nas",
                transport=RcloneSftpTransport(
                    type="rclone_sftp",
                    host="nas.lab.example",
                    user="testuser",
                    remote_path="/srv/nas",
                ),
            ),
        ],
        orchestrator=OrchestratorConfig(
            label="ORCH",
            staging_root=str(staging_root) if enabled else "",
            staging_cleanup=OrchestratorStagingCleanup(),
        ),
    )


def _seed_run(
    staging_root: Path,
    *,
    equipment: str = "EQ1",
    project: str = "PROJ-0001",
    run_name: str = "Run_2026-04-17T14-32-00",
) -> Path:
    run_dir = staging_root / equipment / project / RUNS_DIR_NAME / run_name
    run_dir.mkdir(parents=True)
    (run_dir / "data.bin").write_bytes(b"x" * 100)
    return run_dir


def _write_creation_json(run_dir: Path) -> None:
    """Write a minimal ``creation.json`` so a dir reads as a real run.

    The ``POST /staging/{run}/keep-local`` endpoint guards on the
    presence of this cache (operator-free per-file NAS sync design,
    2026-05-21) before letting ``SyncStateWriter`` create ``sync_state.json``.
    """
    import msgspec

    from exlab_wizard.api.schemas import (
        CreationJson,
        LimsProjectBlock,
        PathsBlock,
        TemplateBlock,
    )
    from exlab_wizard.constants import CREATION_JSON_NAME, CREATION_JSON_VERSION

    cache = run_dir / CACHE_DIR_NAME
    cache.mkdir(parents=True, exist_ok=True)
    payload = CreationJson(
        schema_version=CREATION_JSON_VERSION,
        created_at="2026-05-21T00:00:00Z",
        created_by="asmith",
        level="run",
        run_kind="experimental",
        lims_project=LimsProjectBlock(
            uid="x", short_id="PROJ-0001", name_at_creation="example", source="live"
        ),
        template=TemplateBlock(
            name="basic", version="1.0.0", source_path="/tpl/basic", run_scope="experimental"
        ),
        variables={},
        paths=PathsBlock(local=str(run_dir), nas="/srv/nas/EQ1"),
    )
    (cache / CREATION_JSON_NAME).write_bytes(msgspec.json.encode(payload))


def _seed_real_run(
    staging_root: Path,
    *,
    equipment: str = "EQ1",
    project: str = "PROJ-0001",
    run_name: str = "Run_2026-04-17T14-32-00",
) -> Path:
    """Seed a run dir that carries a ``creation.json`` -- a real run."""
    run_dir = _seed_run(staging_root, equipment=equipment, project=project, run_name=run_name)
    _write_creation_json(run_dir)
    return run_dir


def _mark_synced(run_dir: Path) -> None:
    """Write a fully-verified ``sync_state.json`` so the run rolls up to ``synced``."""
    writer = SyncStateWriter()
    asyncio.run(
        writer.upsert_file(
            run_dir, "data.bin", synced_signature=(100, 1), verified_at="2026-05-21T00:00:00Z"
        )
    )


# ---------------------------------------------------------------------------
# No 503 gate (Redesign §3.1: orchestrator pipeline always on)
# ---------------------------------------------------------------------------


def test_get_staging_returns_empty_when_staging_root_unset(tmp_path: Path) -> None:
    config = _make_config(tmp_path, enabled=False)
    deps = AppDependencies(config=config)
    app = create_app(dependencies=deps)
    with TestClient(app) as client:
        resp = client.get("/api/v1/staging")
    assert resp.status_code == 200
    assert resp.json() == {"runs": []}


def test_force_sync_returns_no_503_when_run_missing(tmp_path: Path) -> None:
    config = _make_config(tmp_path, enabled=False)
    deps = AppDependencies(config=config, nas_sync=_StubNasSync())
    app = create_app(dependencies=deps)
    with TestClient(app) as client:
        resp = client.post("/api/v1/staging/some/path/force-sync")
    assert resp.status_code != 503


def test_clear_returns_no_503_when_run_missing(tmp_path: Path) -> None:
    config = _make_config(tmp_path, enabled=False)
    deps = AppDependencies(config=config)
    app = create_app(dependencies=deps)
    with TestClient(app) as client:
        resp = client.post("/api/v1/staging/some/path/clear")
    assert resp.status_code != 503


# ---------------------------------------------------------------------------
# GET /staging
# ---------------------------------------------------------------------------


def test_get_staging_returns_run_rows(tmp_path: Path) -> None:
    _seed_run(tmp_path)
    config = _make_config(tmp_path)
    deps = AppDependencies(config=config, sync_state_writer=SyncStateWriter())
    app = create_app(dependencies=deps)
    with TestClient(app) as client:
        resp = client.get("/api/v1/staging")
    assert resp.status_code == 200
    body = resp.json()
    assert "runs" in body
    assert len(body["runs"]) == 1
    row = body["runs"][0]
    assert row["equipment_id"] == "EQ1"
    # No sync_state.json yet -> rolls up to "syncing".
    assert row["current_state"] == "syncing"
    assert row["run_kind"] == "experimental"
    assert row["file_count"] == 1
    assert row["byte_total"] == 100


def test_get_staging_derives_current_state_from_sync_state_rollup(tmp_path: Path) -> None:
    run_dir = _seed_run(tmp_path)
    _mark_synced(run_dir)
    config = _make_config(tmp_path)
    deps = AppDependencies(config=config, sync_state_writer=SyncStateWriter())
    app = create_app(dependencies=deps)
    with TestClient(app) as client:
        resp = client.get("/api/v1/staging")
    assert resp.json()["runs"][0]["current_state"] == "synced"


def test_get_staging_returns_empty_runs_for_missing_root(tmp_path: Path) -> None:
    config = _make_config(tmp_path / "missing")
    deps = AppDependencies(config=config)
    app = create_app(dependencies=deps)
    with TestClient(app) as client:
        resp = client.get("/api/v1/staging")
    assert resp.status_code == 200
    assert resp.json() == {"runs": []}


# ---------------------------------------------------------------------------
# POST /staging/{run}/force-sync
# ---------------------------------------------------------------------------


def test_force_sync_invokes_nas_sync_enqueue(tmp_path: Path) -> None:
    run_dir = _seed_run(tmp_path)
    nas_sync = _StubNasSync()
    config = _make_config(tmp_path)
    deps = AppDependencies(config=config, nas_sync=nas_sync)
    app = create_app(dependencies=deps)
    with TestClient(app) as client:
        resp = client.post(f"/api/v1/staging/{run_dir}/force-sync")
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "queued"
    assert body["job_id"] == "job-123"
    assert nas_sync.enqueued == [run_dir]


def test_force_sync_returns_503_when_nas_sync_unwired(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    deps = AppDependencies(config=config)  # no nas_sync
    app = create_app(dependencies=deps)
    with TestClient(app) as client:
        resp = client.post("/api/v1/staging/some/path/force-sync")
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# POST /staging/{run}/clear
# ---------------------------------------------------------------------------


def test_clear_endpoint_deletes_synced_run(tmp_path: Path) -> None:
    run_dir = _seed_run(tmp_path)
    _mark_synced(run_dir)
    config = _make_config(tmp_path)
    deps = AppDependencies(config=config, sync_state_writer=SyncStateWriter())
    app = create_app(dependencies=deps)
    with TestClient(app) as client:
        resp = client.post(f"/api/v1/staging/{run_dir}/clear")
    assert resp.status_code == 200
    body = resp.json()
    assert body["files_freed"] >= 1
    assert body["bytes_freed"] >= 100
    # Data file is gone; the .exlab-wizard/ metadata subtree survives so the
    # cleared run still renders "On NAS" tombstones.
    assert not (run_dir / "data.bin").exists()
    assert (run_dir / CACHE_DIR_NAME).exists()
    # ``cleared_at`` was stamped -> rollup is now CLEARED.
    state = asyncio.run(SyncStateWriter().read(run_dir))
    assert state.cleared_at is not None


def test_clear_endpoint_rejects_non_synced_run(tmp_path: Path) -> None:
    run_dir = _seed_run(tmp_path)  # no sync_state.json -> rollup "syncing"
    config = _make_config(tmp_path)
    deps = AppDependencies(config=config, sync_state_writer=SyncStateWriter())
    app = create_app(dependencies=deps)
    with TestClient(app) as client:
        resp = client.post(f"/api/v1/staging/{run_dir}/clear")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "staging_not_sync_verified"
    assert run_dir.exists()


def test_clear_endpoint_idempotent_for_missing_run(tmp_path: Path) -> None:
    """A run with no queue job and no directory clears to zeros."""
    config = _make_config(tmp_path)
    deps = AppDependencies(config=config)
    app = create_app(dependencies=deps)
    with TestClient(app) as client:
        resp = client.post("/api/v1/staging/missing/path/clear")
    assert resp.status_code == 200
    body = resp.json()
    assert body["files_freed"] == 0
    assert body["bytes_freed"] == 0


# ---------------------------------------------------------------------------
# POST /staging/clear-verified -- Redesign §4.6 bulk action
# ---------------------------------------------------------------------------


def test_clear_verified_endpoint_clears_only_synced_runs(tmp_path: Path) -> None:
    """The bulk endpoint clears every fully-``synced`` run and reports paths."""
    synced_a = _seed_run(tmp_path)
    synced_b = _seed_run(tmp_path, project="PROJ-0002", run_name="Run_2026-05-05")
    syncing_run = _seed_run(tmp_path, equipment="EQ2", project="PROJ-0003")
    _mark_synced(synced_a)
    _mark_synced(synced_b)
    # ``syncing_run`` has no sync_state.json -> rollup "syncing".

    config = _make_config(tmp_path)
    deps = AppDependencies(config=config, sync_state_writer=SyncStateWriter())
    app = create_app(dependencies=deps)
    with TestClient(app) as client:
        resp = client.post("/api/v1/staging/clear-verified")

    assert resp.status_code == 200
    body = resp.json()
    assert set(body["cleared_paths"]) == {str(synced_a), str(synced_b)}
    # Data files are gone; the metadata subtree survives for tombstones.
    assert not (synced_a / "data.bin").exists()
    assert not (synced_b / "data.bin").exists()
    assert (synced_a / CACHE_DIR_NAME).exists()
    # The still-syncing run's data must NOT be touched by the bulk action.
    assert (syncing_run / "data.bin").exists()


def test_clear_verified_endpoint_returns_empty_when_no_verified_runs(tmp_path: Path) -> None:
    """No verified rows -> empty cleared_paths, no error."""
    config = _make_config(tmp_path)  # empty staging_root
    deps = AppDependencies(config=config)
    app = create_app(dependencies=deps)
    with TestClient(app) as client:
        resp = client.post("/api/v1/staging/clear-verified")
    assert resp.status_code == 200
    assert resp.json() == {"cleared_paths": []}


def test_clear_verified_endpoint_returns_503_when_config_unwired(tmp_path: Path) -> None:
    """A deps without a config raises the standard 503."""
    del tmp_path
    deps = AppDependencies(config=None)
    app = create_app(dependencies=deps)
    with TestClient(app) as client:
        resp = client.post("/api/v1/staging/clear-verified")
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# POST /staging/{run_path}/keep-local
# ---------------------------------------------------------------------------


def test_keep_local_toggles_sync_state(tmp_path: Path) -> None:
    """The endpoint flips ``keep_local`` in the run's ``sync_state.json``."""
    run_dir = _seed_real_run(tmp_path)
    writer = SyncStateWriter()
    config = _make_config(tmp_path)
    deps = AppDependencies(config=config, sync_state_writer=writer)
    app = create_app(dependencies=deps)
    with TestClient(app) as client:
        resp = client.post(
            f"/api/v1/staging/{run_dir}/keep-local",
            json={"relative_path": "data.bin", "keep_local": True},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "run_path": str(run_dir),
        "relative_path": "data.bin",
        "keep_local": True,
    }
    state = writer.read_sync(run_dir)
    assert state.files["data.bin"].keep_local is True


def test_keep_local_toggle_off_round_trips(tmp_path: Path) -> None:
    """Setting ``keep_local`` False after True clears the flag on disk."""
    run_dir = _seed_real_run(tmp_path)
    writer = SyncStateWriter()
    asyncio.run(writer.set_keep_local(run_dir, "data.bin", True))
    config = _make_config(tmp_path)
    deps = AppDependencies(config=config, sync_state_writer=writer)
    app = create_app(dependencies=deps)
    with TestClient(app) as client:
        resp = client.post(
            f"/api/v1/staging/{run_dir}/keep-local",
            json={"relative_path": "data.bin", "keep_local": False},
        )
    assert resp.status_code == 200
    assert resp.json()["keep_local"] is False
    assert writer.read_sync(run_dir).files["data.bin"].keep_local is False


def test_keep_local_returns_503_when_writer_unwired(tmp_path: Path) -> None:
    """A deps without a ``sync_state_writer`` raises the standard 503."""
    run_dir = _seed_real_run(tmp_path)
    config = _make_config(tmp_path)
    deps = AppDependencies(config=config)  # no sync_state_writer
    app = create_app(dependencies=deps)
    with TestClient(app) as client:
        resp = client.post(
            f"/api/v1/staging/{run_dir}/keep-local",
            json={"relative_path": "data.bin", "keep_local": True},
        )
    assert resp.status_code == 503


def test_keep_local_returns_503_when_config_unwired(tmp_path: Path) -> None:
    """A deps without a config raises the standard 503."""
    deps = AppDependencies(config=None)
    app = create_app(dependencies=deps)
    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/staging/some/run/keep-local",
            json={"relative_path": "data.bin", "keep_local": True},
        )
    assert resp.status_code == 503


def test_keep_local_rejects_extra_body_fields(tmp_path: Path) -> None:
    """The request model forbids unknown fields."""
    run_dir = _seed_real_run(tmp_path)
    config = _make_config(tmp_path)
    deps = AppDependencies(config=config, sync_state_writer=SyncStateWriter())
    app = create_app(dependencies=deps)
    with TestClient(app) as client:
        resp = client.post(
            f"/api/v1/staging/{run_dir}/keep-local",
            json={"relative_path": "data.bin", "keep_local": True, "bogus": 1},
        )
    assert resp.status_code == 422


def test_keep_local_returns_404_for_path_outside_allowed_roots(tmp_path: Path) -> None:
    """A path outside the configured roots is rejected before any write.

    Operator-free per-file NAS sync design (2026-05-21): ``run_path``
    comes straight from the URL and ``SyncStateWriter`` *creates* the
    run's ``sync_state.json``. A hostile path (``/etc``) must 404, not
    provoke a write under a system directory.
    """
    config = _make_config(tmp_path)
    deps = AppDependencies(config=config, sync_state_writer=SyncStateWriter())
    app = create_app(dependencies=deps)
    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/staging//etc/keep-local",
            json={"relative_path": "passwd", "keep_local": True},
        )
    assert resp.status_code == 404
    # The guard must not have created sync_state.json under /etc.
    assert not (Path("/etc") / CACHE_DIR_NAME / "sync_state.json").exists()


def test_keep_local_returns_404_when_run_has_no_creation_json(tmp_path: Path) -> None:
    """A dir under an allowed root but without a creation.json is not a run."""
    # Inside local_root but no creation.json -> not a real run.
    run_dir = _seed_run(tmp_path)  # _seed_run omits creation.json
    config = _make_config(tmp_path)
    writer = SyncStateWriter()
    deps = AppDependencies(config=config, sync_state_writer=writer)
    app = create_app(dependencies=deps)
    with TestClient(app) as client:
        resp = client.post(
            f"/api/v1/staging/{run_dir}/keep-local",
            json={"relative_path": "data.bin", "keep_local": True},
        )
    assert resp.status_code == 404
    # No sync_state.json was created for the non-run directory.
    assert not (run_dir / CACHE_DIR_NAME / "sync_state.json").exists()


def test_keep_local_creates_sync_state_when_cache_dir_absent(tmp_path: Path) -> None:
    """The writer mkdir's the .exlab-wizard/ dir -- a real run with only a
    creation.json (cache dir exists) toggles cleanly, and even a run whose
    cache dir is later removed does not 500 (S2 robustness)."""
    run_dir = _seed_real_run(tmp_path)
    config = _make_config(tmp_path)
    writer = SyncStateWriter()
    deps = AppDependencies(config=config, sync_state_writer=writer)
    app = create_app(dependencies=deps)
    with TestClient(app) as client:
        resp = client.post(
            f"/api/v1/staging/{run_dir}/keep-local",
            json={"relative_path": "data.bin", "keep_local": True},
        )
    assert resp.status_code == 200
    assert writer.read_sync(run_dir).files["data.bin"].keep_local is True
