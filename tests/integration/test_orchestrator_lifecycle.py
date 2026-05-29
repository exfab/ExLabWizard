"""Integration test for the orchestrator quiescence-sync lifecycle.

Backend Spec §12, §13; operator-free per-file NAS sync design (2026-05-21).
Drives a run through the quiescence poller -> sync-queue enqueue -> the
``GET /staging`` + ``POST /staging/.../clear`` endpoints. Asserts that:

* the :class:`QuiescenceSyncPoller` enqueues a run only once its files
  have settled for ``sync.quiescence_minutes``;
* a quiet file already synced at its current signature (recorded in
  ``sync_state.json``) is not re-enqueued;
* the ``GET /staging`` endpoint surfaces the run with the queue-derived
  ``current_state``, and ``POST /staging/.../clear`` deletes a verified
  run's staging copy.
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
    SyncConfig,
)
from exlab_wizard.constants import RUNS_DIR_NAME
from exlab_wizard.orchestrator.quiescence_poller import QuiescenceSyncPoller


@dataclass
class _Handle:
    job_id: str = "job-1"
    state: str = "queued"
    run_path: str = ""


@dataclass
class _StubJobRow:
    run_path: str
    state: str


class _StubNasSync:
    """In-memory sync client that records per-file enqueue + serves status/list_all."""

    def __init__(self) -> None:
        self.enqueued: list[tuple[Path, list[str]]] = []
        self.status_responses: dict[str, str] = {}

    async def enqueue(self, run_path: Path, files: list[str] | None = None) -> _Handle:
        self.enqueued.append((run_path, list(files or [])))
        return _Handle(run_path=str(run_path))

    async def status(self, run_path: Path) -> str:
        return self.status_responses.get(str(run_path), "none")

    async def list_all(self) -> list[_StubJobRow]:
        return [
            _StubJobRow(run_path=path, state=state) for path, state in self.status_responses.items()
        ]

    @property
    def enqueued_paths(self) -> list[Path]:
        return [run_path for run_path, _ in self.enqueued]


def _make_config(staging_root: Path, *, quiescence_minutes: int = 1) -> Config:
    return Config(
        paths=PathsConfig(local_root=str(staging_root)),
        equipment=[
            EquipmentConfig(
                id="EQ1",
                label="Equipment 1",
                local_root=str(staging_root),
                nas_root="/nas",
            ),
        ],
        orchestrator=OrchestratorConfig(
            label="ORCH",
            staging_root=str(staging_root),
            staging_cleanup=OrchestratorStagingCleanup(),
        ),
        sync=SyncConfig(quiescence_minutes=quiescence_minutes),
    )


def _stage_run(staging_root: Path) -> Path:
    run_dir = staging_root / "EQ1" / "PROJ-0001" / RUNS_DIR_NAME / "Run_2026-04-17T14-32-00"
    run_dir.mkdir(parents=True)
    (run_dir / "data.bin").write_bytes(b"hello-world" * 100)
    return run_dir


# ---------------------------------------------------------------------------
# Quiescence poller end-to-end
# ---------------------------------------------------------------------------


async def test_poller_enqueues_run_after_settle_window(tmp_path: Path) -> None:
    """A staged run is enqueued only once its files settle for the window."""
    config = _make_config(tmp_path, quiescence_minutes=1)  # 60s window
    run_dir = _stage_run(tmp_path)
    nas_sync = _StubNasSync()
    poller = QuiescenceSyncPoller(
        config=config, nas_sync=nas_sync, sync_state_writer=SyncStateWriter()
    )

    # First observation -- not yet quiet.
    assert await poller.poll_once(now_monotonic=0.0) == []
    # Still inside the window.
    assert await poller.poll_once(now_monotonic=45.0) == []
    # Window elapsed -> enqueued exactly once, carrying the quiet file.
    assert await poller.poll_once(now_monotonic=60.0) == [run_dir]
    assert nas_sync.enqueued == [(run_dir, ["data.bin"])]


async def test_poller_skips_file_already_synced_at_current_signature(tmp_path: Path) -> None:
    """A quiet file recorded in sync_state.json at its current signature
    is not re-enqueued; the run drops out of the eligible set."""
    config = _make_config(tmp_path, quiescence_minutes=1)
    run_dir = _stage_run(tmp_path)
    writer = SyncStateWriter()
    target = run_dir / "data.bin"
    st = target.stat()
    await writer.upsert_file(
        run_dir,
        "data.bin",
        synced_signature=(st.st_size, st.st_mtime_ns),
        verified_at="2026-04-17T15:00:00Z",
    )
    nas_sync = _StubNasSync()
    poller = QuiescenceSyncPoller(config=config, nas_sync=nas_sync, sync_state_writer=writer)

    await poller.poll_once(now_monotonic=0.0)
    assert await poller.poll_once(now_monotonic=120.0) == []
    assert nas_sync.enqueued == []


# ---------------------------------------------------------------------------
# Staging API end-to-end
# ---------------------------------------------------------------------------


def _mark_run_synced(run_dir: Path, writer: SyncStateWriter) -> None:
    """Write a fully-verified ``sync_state.json`` so the run rolls up to ``synced``."""
    asyncio.run(
        writer.upsert_file(
            run_dir, "data.bin", synced_signature=(1100, 1), verified_at="2026-05-21T00:00:00Z"
        )
    )


def test_staging_endpoint_surfaces_run_with_rollup_state(tmp_path: Path) -> None:
    """``GET /staging`` reports the run with its ``sync_state.json`` rollup."""
    config = _make_config(tmp_path)
    run_dir = _stage_run(tmp_path)
    writer = SyncStateWriter()
    _mark_run_synced(run_dir, writer)
    deps = AppDependencies(config=config, nas_sync=_StubNasSync(), sync_state_writer=writer)
    app = create_app(dependencies=deps)
    with TestClient(app) as client:
        resp = client.get("/api/v1/staging")
    assert resp.status_code == 200
    runs = resp.json()["runs"]
    assert len(runs) == 1
    assert runs[0]["path"] == str(run_dir)
    assert runs[0]["current_state"] == "synced"


def test_clear_endpoint_deletes_synced_run(tmp_path: Path) -> None:
    """``POST /staging/.../clear`` deletes a fully-``synced`` run's data files,
    retains the metadata subtree, and stamps ``cleared_at``."""
    from exlab_wizard.constants import CACHE_DIR_NAME

    config = _make_config(tmp_path)
    run_dir = _stage_run(tmp_path)
    writer = SyncStateWriter()
    _mark_run_synced(run_dir, writer)
    deps = AppDependencies(config=config, nas_sync=_StubNasSync(), sync_state_writer=writer)
    app = create_app(dependencies=deps)
    with TestClient(app) as client:
        resp = client.post(f"/api/v1/staging/{run_dir}/clear")
    assert resp.status_code == 200
    assert resp.json()["files_freed"] >= 1
    # Data file gone; the .exlab-wizard/ metadata subtree survives so the
    # cleared run still renders "On NAS" tombstones.
    assert not (run_dir / "data.bin").exists()
    assert (run_dir / CACHE_DIR_NAME).exists()
    state = asyncio.run(writer.read(run_dir))
    assert state.cleared_at is not None


def test_clear_endpoint_rejects_unsynced_run(tmp_path: Path) -> None:
    """A run that is not fully ``synced`` cannot be cleared."""
    config = _make_config(tmp_path)
    run_dir = _stage_run(tmp_path)  # no sync_state.json -> rollup "syncing"
    deps = AppDependencies(
        config=config, nas_sync=_StubNasSync(), sync_state_writer=SyncStateWriter()
    )
    app = create_app(dependencies=deps)
    with TestClient(app) as client:
        resp = client.post(f"/api/v1/staging/{run_dir}/clear")
    assert resp.status_code == 409
    assert run_dir.exists()
