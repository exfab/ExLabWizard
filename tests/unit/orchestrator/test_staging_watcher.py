"""Unit tests for ``exlab_wizard.orchestrator.staging_watcher``.

Backend Spec §13.3, §13.5, §13.7. Each five-state transition is driven
synchronously through :meth:`StagingWatcher.evaluate_run` so we can
assert the on-disk effect without spinning up the polling task.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import msgspec

from exlab_wizard.api.schemas import (
    CreationJson,
    IngestJson,
)
from exlab_wizard.cache.ingest_writer import IngestWriter
from exlab_wizard.config.models import (
    BandwidthConfig,
    Config,
    EquipmentConfig,
    OrchestratorConfig,
    OrchestratorStagingCleanup,
    OrchestratorStagingTransport,
    PathsConfig,
    RcloneTransport,
)
from exlab_wizard.constants import (
    CACHE_DIR_NAME,
    CREATION_JSON_NAME,
    CREATION_JSON_VERSION,
    INGEST_JSON_NAME,
    INGEST_JSON_VERSION,
    IngestState,
    StagingCleanupMode,
)
from exlab_wizard.orchestrator.staging_watcher import StagingWatcher

# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


@dataclass
class StubHandle:
    job_id: str = "job-1"
    state: str = "queued"
    run_path: str = ""
    blocking_findings: tuple = ()


class StubNasSync:
    """Simple in-memory NAS sync client matching the protocol."""

    def __init__(self) -> None:
        self.enqueue_calls: list[Path] = []
        self.status_responses: dict[str, str] = {}

    async def enqueue(self, run_path: Path) -> StubHandle:
        self.enqueue_calls.append(run_path)
        return StubHandle(run_path=str(run_path))

    async def status(self, run_path: Path) -> str:
        return self.status_responses.get(str(run_path), "queued")


class StubCreationCache:
    """Returns a hand-built CreationJson when read."""

    def __init__(self, *, payload: CreationJson | None = None) -> None:
        self._payload = payload

    async def read_creation_snapshot(self, path: Path) -> CreationJson:
        if self._payload is None:
            raise FileNotFoundError(path)
        return self._payload


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_config(
    staging_root: Path,
    *,
    completeness_signal: str = "sentinel_file",
    sentinel_filename: str = "run_complete",
    manifest_filename: str | None = None,
    cleanup_mode: str = StagingCleanupMode.MANUAL.value,
    retain_hours: int = 24,
    enabled: bool = True,
) -> Config:
    return Config(
        paths=PathsConfig(local_root=str(staging_root)),
        equipment=[
            EquipmentConfig(
                id="EQ1",
                label="Equipment 1",
                local_root=str(staging_root),
                nas_root="/nas",
                completeness_signal=completeness_signal,
                sentinel_filename=sentinel_filename
                if completeness_signal == "sentinel_file"
                else None,
                manifest_filename=manifest_filename if completeness_signal == "manifest" else None,
                transport=RcloneTransport(
                    type="rclone",
                    rclone_remote="lab-nas",
                    rclone_remote_path="/srv/nas",
                    bandwidth=BandwidthConfig(),
                ),
            ),
        ],
        orchestrator=OrchestratorConfig(
            enabled=enabled,
            label="ORCH",
            staging_root=str(staging_root),
            staging_cleanup=OrchestratorStagingCleanup(
                mode=cleanup_mode, retain_hours=retain_hours
            ),
        ),
    )


def _make_creation() -> CreationJson:
    return msgspec.convert(
        {
            "schema_version": CREATION_JSON_VERSION,
            "created_at": "2026-04-17T14:32:00Z",
            "created_by": "asmith",
            "level": "run",
            "run_kind": "experimental",
            "lims_project": {
                "uid": "abc",
                "short_id": "PROJ-0001",
                "name_at_creation": "Test Project",
            },
            "template": {
                "name": "confocal_run",
                "version": "1.0",
                "source_path": "templates/confocal_run",
                "run_scope": "experimental",
            },
            "variables": {},
            "paths": {
                "local": "/mnt/staging/EQ1/PROJ-0001/Run_2026-04-17T14-32-00",
                "nas": "/nas/EQ1/PROJ-0001/Run_2026-04-17T14-32-00",
            },
        },
        type=CreationJson,
    )


def _seed_pushed_run(
    staging_root: Path, *, equipment: str = "EQ1", project: str = "PROJ-0001"
) -> Path:
    """Create the on-disk push that an equipment machine would produce."""
    run_dir = staging_root / equipment / project / "Run_2026-04-17T14-32-00"
    run_dir.mkdir(parents=True)
    (run_dir / "data.bin").write_bytes(b"abcd" * 256)
    cache = run_dir / CACHE_DIR_NAME
    cache.mkdir()
    creation = _make_creation()
    (cache / CREATION_JSON_NAME).write_bytes(msgspec.json.encode(creation))
    return run_dir


def _read_ingest(run_dir: Path) -> IngestJson:
    return msgspec.json.decode(
        (run_dir / CACHE_DIR_NAME / INGEST_JSON_NAME).read_bytes(),
        type=IngestJson,
    )


# ---------------------------------------------------------------------------
# Bootstrap (no ingest.json yet -> writes the initial staging payload)
# ---------------------------------------------------------------------------


async def test_evaluate_run_bootstraps_initial_ingest_json(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    run_dir = _seed_pushed_run(tmp_path)
    watcher = StagingWatcher(
        config=config,
        ingest_writer=IngestWriter(),
        nas_sync=StubNasSync(),
        cache_creation=StubCreationCache(payload=_make_creation()),
    )

    state = await watcher.evaluate_run(run_dir)

    assert state == IngestState.STAGING
    ingest = _read_ingest(run_dir)
    assert ingest.current_state == IngestState.STAGING.value
    assert ingest.equipment_id == "EQ1"
    assert ingest.run_kind == "experimental"
    assert ingest.transport == "smb_mount"
    assert len(ingest.history) == 1
    assert ingest.history[0]["state"] == IngestState.STAGING.value


async def test_evaluate_run_bootstraps_when_creation_json_missing(tmp_path: Path) -> None:
    """Equipment push that has no creation.json yet still gets a staging entry."""
    config = _make_config(tmp_path)
    run_dir = tmp_path / "EQ1" / "PROJ-0001" / "Run_2026-04-17T14-32-00"
    run_dir.mkdir(parents=True)
    watcher = StagingWatcher(
        config=config,
        ingest_writer=IngestWriter(),
        nas_sync=StubNasSync(),
        cache_creation=StubCreationCache(payload=None),
    )

    state = await watcher.evaluate_run(run_dir)

    assert state == IngestState.STAGING
    ingest = _read_ingest(run_dir)
    assert ingest.equipment_id == "EQ1"


async def test_evaluate_run_returns_staging_for_unrecognised_path(tmp_path: Path) -> None:
    """A path whose leaf doesn't start with Run_ / TestRun_ is a no-op."""
    config = _make_config(tmp_path)
    bogus = tmp_path / "EQ1" / "PROJ-0001" / "NotARun"
    bogus.mkdir(parents=True)
    watcher = StagingWatcher(
        config=config,
        ingest_writer=IngestWriter(),
        nas_sync=StubNasSync(),
        cache_creation=StubCreationCache(),
    )
    state = await watcher.evaluate_run(bogus)
    assert state == IngestState.STAGING


# ---------------------------------------------------------------------------
# staging -> complete (sentinel file)
# ---------------------------------------------------------------------------


async def test_evaluate_run_advances_to_complete_when_sentinel_file_present(tmp_path: Path) -> None:
    config = _make_config(tmp_path, sentinel_filename="run_complete")
    run_dir = _seed_pushed_run(tmp_path)
    watcher = StagingWatcher(
        config=config,
        ingest_writer=IngestWriter(),
        nas_sync=StubNasSync(),
        cache_creation=StubCreationCache(payload=_make_creation()),
    )
    # First call writes the staging entry.
    await watcher.evaluate_run(run_dir)
    # No sentinel yet -- still staging.
    state = await watcher.evaluate_run(run_dir)
    assert state == IngestState.STAGING

    # Equipment writes the sentinel.
    (run_dir / "run_complete").write_text("done")
    state = await watcher.evaluate_run(run_dir)
    assert state == IngestState.COMPLETE
    ingest = _read_ingest(run_dir)
    last = ingest.history[-1]
    assert last["state"] == IngestState.COMPLETE.value
    assert last["files_received"] >= 1
    assert last["bytes_received"] >= 4 * 256


# ---------------------------------------------------------------------------
# staging -> complete (manifest comparison)
# ---------------------------------------------------------------------------


async def test_evaluate_run_advances_to_complete_when_manifest_satisfied(tmp_path: Path) -> None:
    config = _make_config(
        tmp_path,
        completeness_signal="manifest",
        sentinel_filename="",
        manifest_filename="manifest.json",
    )
    run_dir = _seed_pushed_run(tmp_path)
    watcher = StagingWatcher(
        config=config,
        ingest_writer=IngestWriter(),
        nas_sync=StubNasSync(),
        cache_creation=StubCreationCache(payload=_make_creation()),
    )
    await watcher.evaluate_run(run_dir)  # bootstrap

    # Write a manifest listing the existing data.bin with its actual size.
    manifest = {"files": [{"path": "data.bin", "size": (run_dir / "data.bin").stat().st_size}]}
    (run_dir / "manifest.json").write_bytes(msgspec.json.encode(manifest))

    state = await watcher.evaluate_run(run_dir)
    assert state == IngestState.COMPLETE


async def test_evaluate_run_stays_staging_when_manifest_size_mismatches(tmp_path: Path) -> None:
    config = _make_config(
        tmp_path,
        completeness_signal="manifest",
        sentinel_filename="",
        manifest_filename="manifest.json",
    )
    run_dir = _seed_pushed_run(tmp_path)
    watcher = StagingWatcher(
        config=config,
        ingest_writer=IngestWriter(),
        nas_sync=StubNasSync(),
        cache_creation=StubCreationCache(payload=_make_creation()),
    )
    await watcher.evaluate_run(run_dir)
    manifest = {"files": [{"path": "data.bin", "size": 999_999_999}]}
    (run_dir / "manifest.json").write_bytes(msgspec.json.encode(manifest))

    state = await watcher.evaluate_run(run_dir)
    assert state == IngestState.STAGING


async def test_evaluate_run_stays_staging_when_manifest_lists_missing_file(tmp_path: Path) -> None:
    config = _make_config(
        tmp_path,
        completeness_signal="manifest",
        sentinel_filename="",
        manifest_filename="manifest.json",
    )
    run_dir = _seed_pushed_run(tmp_path)
    watcher = StagingWatcher(
        config=config,
        ingest_writer=IngestWriter(),
        nas_sync=StubNasSync(),
        cache_creation=StubCreationCache(payload=_make_creation()),
    )
    await watcher.evaluate_run(run_dir)
    manifest = {"files": [{"path": "missing.bin", "size": 100}]}
    (run_dir / "manifest.json").write_bytes(msgspec.json.encode(manifest))

    state = await watcher.evaluate_run(run_dir)
    assert state == IngestState.STAGING


# ---------------------------------------------------------------------------
# complete -> sync_queued
# ---------------------------------------------------------------------------


async def test_evaluate_run_advances_to_sync_queued_after_enqueue(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    run_dir = _seed_pushed_run(tmp_path)
    nas_sync = StubNasSync()
    watcher = StagingWatcher(
        config=config,
        ingest_writer=IngestWriter(),
        nas_sync=nas_sync,
        cache_creation=StubCreationCache(payload=_make_creation()),
    )
    await watcher.evaluate_run(run_dir)
    (run_dir / "run_complete").write_text("done")
    await watcher.evaluate_run(run_dir)  # -> complete

    state = await watcher.evaluate_run(run_dir)
    assert state == IngestState.SYNC_QUEUED
    assert nas_sync.enqueue_calls == [run_dir]


# ---------------------------------------------------------------------------
# sync_queued -> sync_verified
# ---------------------------------------------------------------------------


async def test_evaluate_run_advances_to_sync_verified_when_status_verified(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    run_dir = _seed_pushed_run(tmp_path)
    nas_sync = StubNasSync()
    watcher = StagingWatcher(
        config=config,
        ingest_writer=IngestWriter(),
        nas_sync=nas_sync,
        cache_creation=StubCreationCache(payload=_make_creation()),
    )
    await watcher.evaluate_run(run_dir)
    (run_dir / "run_complete").write_text("done")
    await watcher.evaluate_run(run_dir)
    await watcher.evaluate_run(run_dir)  # -> sync_queued

    # Without the verified status, no transition.
    nas_sync.status_responses[str(run_dir)] = "running"
    state = await watcher.evaluate_run(run_dir)
    assert state == IngestState.SYNC_QUEUED

    # With verified status, advances.
    nas_sync.status_responses[str(run_dir)] = "verified"
    state = await watcher.evaluate_run(run_dir)
    assert state == IngestState.SYNC_VERIFIED


async def test_evaluate_run_treats_cleaned_status_as_verified(tmp_path: Path) -> None:
    """Per §7.1.2 the cleanup states still mean the NAS copy is durable."""
    config = _make_config(tmp_path)
    run_dir = _seed_pushed_run(tmp_path)
    nas_sync = StubNasSync()
    watcher = StagingWatcher(
        config=config,
        ingest_writer=IngestWriter(),
        nas_sync=nas_sync,
        cache_creation=StubCreationCache(payload=_make_creation()),
    )
    await watcher.evaluate_run(run_dir)
    (run_dir / "run_complete").write_text("done")
    await watcher.evaluate_run(run_dir)
    await watcher.evaluate_run(run_dir)
    nas_sync.status_responses[str(run_dir)] = "cleaned"

    state = await watcher.evaluate_run(run_dir)
    assert state == IngestState.SYNC_VERIFIED


# ---------------------------------------------------------------------------
# sync_verified -> cleared (manual + scheduled)
# ---------------------------------------------------------------------------


async def test_evaluate_run_does_not_clear_in_manual_mode(tmp_path: Path) -> None:
    config = _make_config(tmp_path, cleanup_mode=StagingCleanupMode.MANUAL.value)
    run_dir = _seed_pushed_run(tmp_path)
    nas_sync = StubNasSync()
    watcher = StagingWatcher(
        config=config,
        ingest_writer=IngestWriter(),
        nas_sync=nas_sync,
        cache_creation=StubCreationCache(payload=_make_creation()),
    )
    # Walk forward to sync_verified.
    await watcher.evaluate_run(run_dir)
    (run_dir / "run_complete").write_text("done")
    await watcher.evaluate_run(run_dir)
    await watcher.evaluate_run(run_dir)
    nas_sync.status_responses[str(run_dir)] = "verified"
    await watcher.evaluate_run(run_dir)

    # Even after another tick, manual mode does not auto-clear.
    state = await watcher.evaluate_run(run_dir)
    assert state == IngestState.SYNC_VERIFIED
    assert run_dir.exists()


async def test_evaluate_run_clears_in_scheduled_mode_after_retain_hours(tmp_path: Path) -> None:
    config = _make_config(
        tmp_path,
        cleanup_mode=StagingCleanupMode.SCHEDULED.value,
        retain_hours=1,
    )
    run_dir = _seed_pushed_run(tmp_path)
    nas_sync = StubNasSync()
    watcher = StagingWatcher(
        config=config,
        ingest_writer=IngestWriter(),
        nas_sync=nas_sync,
        cache_creation=StubCreationCache(payload=_make_creation()),
    )
    await watcher.evaluate_run(run_dir)
    (run_dir / "run_complete").write_text("done")
    await watcher.evaluate_run(run_dir)
    await watcher.evaluate_run(run_dir)
    nas_sync.status_responses[str(run_dir)] = "verified"
    await watcher.evaluate_run(run_dir)

    # Patch the on-disk sync_verified entry to be 2 hours old.
    cache_path = run_dir / CACHE_DIR_NAME / INGEST_JSON_NAME
    payload = msgspec.json.decode(cache_path.read_bytes(), type=IngestJson)
    backdated_at = (datetime.now(tz=UTC) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    new_history = []
    for entry in payload.history:
        new_entry = dict(entry)
        if entry.get("state") == IngestState.SYNC_VERIFIED.value:
            new_entry["at"] = backdated_at
        new_history.append(new_entry)
    new_payload = msgspec.structs.replace(payload, history=new_history)
    cache_path.write_bytes(msgspec.json.encode(new_payload))

    state = await watcher.evaluate_run(run_dir)
    assert state == IngestState.CLEARED
    assert not run_dir.exists()


# ---------------------------------------------------------------------------
# cleared is terminal
# ---------------------------------------------------------------------------


async def test_evaluate_run_returns_cleared_when_already_cleared(tmp_path: Path) -> None:
    """A run whose ingest says cleared but whose dir lingers is a no-op."""
    config = _make_config(tmp_path)
    run_dir = _seed_pushed_run(tmp_path)
    cache = run_dir / CACHE_DIR_NAME
    payload = msgspec.convert(
        {
            "schema_version": INGEST_JSON_VERSION,
            "project_name": "Test",
            "equipment_id": "EQ1",
            "run_kind": "experimental",
            "run_path": str(run_dir),
            "transport": "smb_mount",
            "current_state": IngestState.CLEARED.value,
            "history": [
                {"state": IngestState.CLEARED.value, "at": "2026-04-17T14:00:00Z", "host": "h"}
            ],
        },
        type=IngestJson,
    )
    await IngestWriter().write_ingest(cache / INGEST_JSON_NAME, payload)

    watcher = StagingWatcher(
        config=config,
        ingest_writer=IngestWriter(),
        nas_sync=StubNasSync(),
        cache_creation=StubCreationCache(),
    )
    state = await watcher.evaluate_run(run_dir)
    assert state == IngestState.CLEARED


# ---------------------------------------------------------------------------
# poll_once + on_state_change hook
# ---------------------------------------------------------------------------


async def test_poll_once_returns_state_per_run(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    _seed_pushed_run(tmp_path, equipment="EQ1", project="PROJ-0001")
    # Add a second equipment in the config + on disk.
    config.equipment.append(
        EquipmentConfig(
            id="EQ2",
            label="Equipment 2",
            local_root=str(tmp_path),
            nas_root="/nas",
            completeness_signal="sentinel_file",
            sentinel_filename="run_complete",
            transport=RcloneTransport(
                type="rclone",
                rclone_remote="lab-nas",
                rclone_remote_path="/srv/nas",
            ),
        )
    )
    run2 = tmp_path / "EQ2" / "PROJ-0002" / "Run_2026-04-18T00-00-00"
    run2.mkdir(parents=True)
    cache2 = run2 / CACHE_DIR_NAME
    cache2.mkdir()
    (cache2 / CREATION_JSON_NAME).write_bytes(msgspec.json.encode(_make_creation()))

    watcher = StagingWatcher(
        config=config,
        ingest_writer=IngestWriter(),
        nas_sync=StubNasSync(),
        cache_creation=StubCreationCache(payload=_make_creation()),
    )
    states = await watcher.poll_once()
    assert states == [IngestState.STAGING, IngestState.STAGING]


async def test_on_state_change_called_for_each_transition(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    run_dir = _seed_pushed_run(tmp_path)
    captured: list[tuple[Path, IngestState]] = []

    async def hook(path: Path, state: IngestState) -> None:
        captured.append((path, state))

    watcher = StagingWatcher(
        config=config,
        ingest_writer=IngestWriter(),
        nas_sync=StubNasSync(),
        cache_creation=StubCreationCache(payload=_make_creation()),
        on_state_change=hook,
    )
    await watcher.evaluate_run(run_dir)
    (run_dir / "run_complete").write_text("done")
    await watcher.evaluate_run(run_dir)

    states_seen = [s for (_, s) in captured]
    assert IngestState.STAGING in states_seen
    assert IngestState.COMPLETE in states_seen


def test_on_state_change_supports_sync_callable(tmp_path: Path) -> None:
    """A non-async callback must also be invoked correctly."""
    config = _make_config(tmp_path)
    captured: list[Any] = []

    def hook(path: Path, state: IngestState) -> None:
        captured.append((path, state))

    watcher = StagingWatcher(
        config=config,
        ingest_writer=IngestWriter(),
        nas_sync=StubNasSync(),
        cache_creation=StubCreationCache(payload=_make_creation()),
        on_state_change=hook,
    )

    async def driver() -> None:
        run_dir = _seed_pushed_run(tmp_path)
        await watcher.evaluate_run(run_dir)

    asyncio.run(driver())
    assert captured  # at least the staging transition was recorded


# ---------------------------------------------------------------------------
# start / stop lifecycle
# ---------------------------------------------------------------------------


async def test_start_does_nothing_when_orchestrator_disabled(tmp_path: Path) -> None:
    config = _make_config(tmp_path, enabled=False)
    watcher = StagingWatcher(
        config=config,
        ingest_writer=IngestWriter(),
        nas_sync=StubNasSync(),
        cache_creation=StubCreationCache(),
    )
    await watcher.start()
    # Idempotent stop on a watcher that never started.
    await watcher.stop()


async def test_start_then_stop_runs_the_loop(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    watcher = StagingWatcher(
        config=config,
        ingest_writer=IngestWriter(),
        nas_sync=StubNasSync(),
        cache_creation=StubCreationCache(),
        poll_interval_s=0.01,
    )
    await watcher.start()
    # Give the loop a moment to tick at least once.
    await asyncio.sleep(0.05)
    await watcher.stop()


async def test_start_is_idempotent(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    watcher = StagingWatcher(
        config=config,
        ingest_writer=IngestWriter(),
        nas_sync=StubNasSync(),
        cache_creation=StubCreationCache(),
        poll_interval_s=0.01,
    )
    await watcher.start()
    await watcher.start()  # second call must be a no-op
    await watcher.stop()


# ---------------------------------------------------------------------------
# Edge case: equipment without orchestrator_staging_transport
# ---------------------------------------------------------------------------


async def test_evaluate_run_uses_default_transport_when_unset(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    config.equipment[0] = EquipmentConfig(
        id="EQ1",
        label="Equipment 1",
        local_root=str(tmp_path),
        nas_root="/nas",
        completeness_signal="sentinel_file",
        sentinel_filename="run_complete",
        transport=RcloneTransport(
            type="rclone",
            rclone_remote="lab-nas",
            rclone_remote_path="/srv/nas",
        ),
        # No orchestrator_staging_transport.
    )
    run_dir = _seed_pushed_run(tmp_path)
    watcher = StagingWatcher(
        config=config,
        ingest_writer=IngestWriter(),
        nas_sync=StubNasSync(),
        cache_creation=StubCreationCache(payload=_make_creation()),
    )
    await watcher.evaluate_run(run_dir)
    ingest = _read_ingest(run_dir)
    assert ingest.transport == "smb_mount"


async def test_evaluate_run_handles_path_outside_staging_root(tmp_path: Path) -> None:
    """A run whose absolute path is not inside the staging tree is a no-op."""
    config = _make_config(tmp_path)
    watcher = StagingWatcher(
        config=config,
        ingest_writer=IngestWriter(),
        nas_sync=StubNasSync(),
        cache_creation=StubCreationCache(),
    )
    outside = tmp_path.parent / "outside" / "Run_2026-04-17T14-32-00"
    outside.mkdir(parents=True, exist_ok=True)
    state = await watcher.evaluate_run(outside)
    assert state == IngestState.STAGING


async def test_evaluate_run_advances_when_manifest_with_no_files_present(tmp_path: Path) -> None:
    """An empty-files manifest means 'no files expected'; sentinel alone is enough."""
    config = _make_config(
        tmp_path,
        completeness_signal="manifest",
        sentinel_filename="",
        manifest_filename="manifest.json",
    )
    run_dir = _seed_pushed_run(tmp_path)
    watcher = StagingWatcher(
        config=config,
        ingest_writer=IngestWriter(),
        nas_sync=StubNasSync(),
        cache_creation=StubCreationCache(payload=_make_creation()),
    )
    await watcher.evaluate_run(run_dir)
    (run_dir / "manifest.json").write_bytes(msgspec.json.encode({"files": []}))
    state = await watcher.evaluate_run(run_dir)
    assert state == IngestState.COMPLETE


async def test_evaluate_run_stays_staging_when_manifest_malformed(tmp_path: Path) -> None:
    config = _make_config(
        tmp_path,
        completeness_signal="manifest",
        sentinel_filename="",
        manifest_filename="manifest.json",
    )
    run_dir = _seed_pushed_run(tmp_path)
    watcher = StagingWatcher(
        config=config,
        ingest_writer=IngestWriter(),
        nas_sync=StubNasSync(),
        cache_creation=StubCreationCache(payload=_make_creation()),
    )
    await watcher.evaluate_run(run_dir)
    (run_dir / "manifest.json").write_bytes(b"not json {")
    state = await watcher.evaluate_run(run_dir)
    assert state == IngestState.STAGING


async def test_completeness_signal_unknown_equipment_returns_false(tmp_path: Path) -> None:
    """A run whose equipment id isn't configured stays staging."""
    config = _make_config(tmp_path)
    run_dir = tmp_path / "UNKNOWN" / "PROJ-0001" / "Run_2026-04-17T14-32-00"
    run_dir.mkdir(parents=True)
    cache = run_dir / CACHE_DIR_NAME
    cache.mkdir()
    (cache / CREATION_JSON_NAME).write_bytes(msgspec.json.encode(_make_creation()))

    watcher = StagingWatcher(
        config=config,
        ingest_writer=IngestWriter(),
        nas_sync=StubNasSync(),
        cache_creation=StubCreationCache(payload=_make_creation()),
    )
    await watcher.evaluate_run(run_dir)
    # Even with a sentinel present, an unknown equipment never advances.
    (run_dir / "run_complete").write_text("done")
    state = await watcher.evaluate_run(run_dir)
    assert state == IngestState.STAGING
