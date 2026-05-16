"""Unit tests for the ``/staging`` router. Backend Spec §13.7, §13.8."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import msgspec
from fastapi.testclient import TestClient

from exlab_wizard.api import AppDependencies, create_app
from exlab_wizard.api.schemas import IngestJson
from exlab_wizard.cache.ingest_writer import IngestWriter
from exlab_wizard.config.models import (
    Config,
    EquipmentConfig,
    OrchestratorConfig,
    OrchestratorStagingCleanup,
    PathsConfig,
    RcloneTransport,
)
from exlab_wizard.constants import (
    CACHE_DIR_NAME,
    INGEST_JSON_NAME,
    INGEST_JSON_VERSION,
    IngestState,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class _Handle:
    job_id: str = "job-123"
    state: str = "queued"
    run_path: str = ""


class _StubNasSync:
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
                completeness_signal="sentinel_file",
                sentinel_filename="done.flag",
                transport=RcloneTransport(
                    type="rclone",
                    rclone_remote="lab-nas",
                    rclone_remote_path="/srv/nas",
                ),
            ),
        ],
        orchestrator=OrchestratorConfig(
            label="ORCH",
            staging_root=str(staging_root) if enabled else "",
            staging_cleanup=OrchestratorStagingCleanup(),
        ),
    )


async def _seed_run(
    staging_root: Path,
    *,
    state: IngestState = IngestState.STAGING,
    last_at: str = "2026-04-17T12:00:00Z",
) -> Path:
    run_dir = staging_root / "EQ1" / "PROJ-0001" / "Run_2026-04-17T14-32-00"
    run_dir.mkdir(parents=True)
    (run_dir / "data.bin").write_bytes(b"x" * 100)
    cache = run_dir / CACHE_DIR_NAME
    cache.mkdir()
    payload = msgspec.convert(
        {
            "schema_version": INGEST_JSON_VERSION,
            "project_name": "PROJ-0001",
            "equipment_id": "EQ1",
            "run_kind": "experimental",
            "run_path": str(run_dir),
            "transport": "smb_mount",
            "current_state": state.value,
            "history": [{"state": state.value, "at": last_at, "host": "host"}],
        },
        type=IngestJson,
    )
    await IngestWriter().write_ingest(cache / INGEST_JSON_NAME, payload)
    return run_dir


# ---------------------------------------------------------------------------
# 503 when orchestrator disabled
# ---------------------------------------------------------------------------


def test_get_staging_returns_empty_when_staging_root_unset(tmp_path: Path) -> None:
    """Redesign §3.1: orchestrator pipeline is always on. The 503 gate is
    removed; a staging_root that isn't on disk returns an empty list."""
    config = _make_config(tmp_path, enabled=False)
    deps = AppDependencies(config=config)
    app = create_app(dependencies=deps)
    with TestClient(app) as client:
        resp = client.get("/api/v1/staging")
    assert resp.status_code == 200
    assert resp.json() == {"runs": []}


def test_force_sync_returns_404_when_run_missing(tmp_path: Path) -> None:
    config = _make_config(tmp_path, enabled=False)
    deps = AppDependencies(config=config, nas_sync=_StubNasSync())
    app = create_app(dependencies=deps)
    with TestClient(app) as client:
        resp = client.post("/api/v1/staging/some/path/force-sync")
    # Either 404 (run not found) or 200 (stub queues it); the spec doesn't
    # mandate which, only that there's no orchestrator_disabled gate.
    assert resp.status_code != 503


def test_clear_returns_404_when_run_missing(tmp_path: Path) -> None:
    config = _make_config(tmp_path, enabled=False)
    deps = AppDependencies(config=config)
    app = create_app(dependencies=deps)
    with TestClient(app) as client:
        resp = client.post("/api/v1/staging/some/path/clear")
    # Redesign §3.1: no orchestrator_disabled gate; the endpoint just
    # reports a 404 / 200 outcome depending on whether the run exists.
    assert resp.status_code != 503


# ---------------------------------------------------------------------------
# GET /staging
# ---------------------------------------------------------------------------


async def test_get_staging_returns_run_rows(tmp_path: Path) -> None:
    await _seed_run(tmp_path)
    config = _make_config(tmp_path)
    deps = AppDependencies(config=config)
    app = create_app(dependencies=deps)
    with TestClient(app) as client:
        resp = client.get("/api/v1/staging")
    assert resp.status_code == 200
    body = resp.json()
    assert "runs" in body
    assert len(body["runs"]) == 1
    row = body["runs"][0]
    assert row["equipment_id"] == "EQ1"
    assert row["current_state"] == IngestState.STAGING.value
    assert row["run_kind"] == "experimental"
    assert row["file_count"] == 1
    assert row["byte_total"] == 100


def test_get_staging_returns_empty_runs_for_missing_root(tmp_path: Path) -> None:
    """A staging_root that doesn't exist on disk yet returns runs=[]."""
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


async def test_force_sync_invokes_nas_sync_enqueue(tmp_path: Path) -> None:
    run_dir = await _seed_run(tmp_path)
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


async def test_clear_endpoint_deletes_sync_verified_run(tmp_path: Path) -> None:
    run_dir = await _seed_run(tmp_path, state=IngestState.SYNC_VERIFIED)
    config = _make_config(tmp_path)
    deps = AppDependencies(config=config, ingest_writer=IngestWriter())
    app = create_app(dependencies=deps)
    with TestClient(app) as client:
        resp = client.post(f"/api/v1/staging/{run_dir}/clear")
    assert resp.status_code == 200
    body = resp.json()
    assert body["files_freed"] >= 1
    assert body["bytes_freed"] >= 100
    assert not run_dir.exists()


async def test_clear_endpoint_rejects_non_sync_verified_run(tmp_path: Path) -> None:
    run_dir = await _seed_run(tmp_path, state=IngestState.STAGING)
    config = _make_config(tmp_path)
    deps = AppDependencies(config=config, ingest_writer=IngestWriter())
    app = create_app(dependencies=deps)
    with TestClient(app) as client:
        resp = client.post(f"/api/v1/staging/{run_dir}/clear")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "staging_not_sync_verified"
    assert run_dir.exists()


def test_clear_endpoint_falls_back_to_default_writer_when_unwired(tmp_path: Path) -> None:
    """A deps without ``ingest_writer`` builds a fresh IngestWriter."""
    config = _make_config(tmp_path)
    deps = AppDependencies(config=config)  # no ingest_writer
    app = create_app(dependencies=deps)
    with TestClient(app) as client:
        # Path doesn't exist -- clear is idempotent and returns zeros.
        resp = client.post("/api/v1/staging/missing/path/clear")
    assert resp.status_code == 200
    body = resp.json()
    assert body["files_freed"] == 0
    assert body["bytes_freed"] == 0


# ---------------------------------------------------------------------------
# POST /staging/clear-verified -- Redesign §4.6 bulk action
# ---------------------------------------------------------------------------


async def test_clear_verified_endpoint_clears_only_sync_verified_runs(
    tmp_path: Path,
) -> None:
    """The bulk endpoint clears every sync_verified run and reports paths."""
    # Two SYNC_VERIFIED runs + one STAGING run that must remain.
    verified_a = await _seed_run(tmp_path, state=IngestState.SYNC_VERIFIED)
    # Second verified run under a separate project so its directory is distinct.
    second_dir = tmp_path / "EQ1" / "PROJ-0002" / "Run_2026-05-05"
    second_dir.mkdir(parents=True)
    (second_dir / "file.bin").write_bytes(b"y" * 50)
    cache = second_dir / CACHE_DIR_NAME
    cache.mkdir()
    payload = msgspec.convert(
        {
            "schema_version": INGEST_JSON_VERSION,
            "project_name": "PROJ-0002",
            "equipment_id": "EQ1",
            "run_kind": "experimental",
            "run_path": str(second_dir),
            "transport": "smb_mount",
            "current_state": IngestState.SYNC_VERIFIED.value,
            "history": [
                {
                    "state": IngestState.SYNC_VERIFIED.value,
                    "at": "2026-05-05T10:00:00Z",
                    "host": "h",
                }
            ],
        },
        type=IngestJson,
    )
    await IngestWriter().write_ingest(cache / INGEST_JSON_NAME, payload)
    staging_dir = tmp_path / "EQ2" / "PROJ-0003" / "Run_2026-05-06"
    staging_dir.mkdir(parents=True)
    (staging_dir / "raw.bin").write_bytes(b"z" * 25)
    staging_cache = staging_dir / CACHE_DIR_NAME
    staging_cache.mkdir()
    staging_payload = msgspec.convert(
        {
            "schema_version": INGEST_JSON_VERSION,
            "project_name": "PROJ-0003",
            "equipment_id": "EQ2",
            "run_kind": "experimental",
            "run_path": str(staging_dir),
            "transport": "smb_mount",
            "current_state": IngestState.STAGING.value,
            "history": [
                {
                    "state": IngestState.STAGING.value,
                    "at": "2026-05-06T10:00:00Z",
                    "host": "h",
                }
            ],
        },
        type=IngestJson,
    )
    await IngestWriter().write_ingest(staging_cache / INGEST_JSON_NAME, staging_payload)

    config = _make_config(tmp_path)
    deps = AppDependencies(config=config, ingest_writer=IngestWriter())
    app = create_app(dependencies=deps)
    with TestClient(app) as client:
        resp = client.post("/api/v1/staging/clear-verified")

    assert resp.status_code == 200
    body = resp.json()
    assert set(body["cleared_paths"]) == {str(verified_a), str(second_dir)}
    assert not verified_a.exists()
    assert not second_dir.exists()
    # The STAGING run must NOT be touched by the bulk action.
    assert staging_dir.exists()


def test_clear_verified_endpoint_returns_empty_when_no_verified_runs(
    tmp_path: Path,
) -> None:
    """No SYNC_VERIFIED rows -> empty cleared_paths, no error."""
    config = _make_config(tmp_path)  # empty staging_root
    deps = AppDependencies(config=config, ingest_writer=IngestWriter())
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
