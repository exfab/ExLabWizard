"""Unit tests for ``exlab_wizard.orchestrator.cleanup``.

Backend Spec §13.7. Covers the manual / scheduled policy decisions,
the on-disk delete, and the file-count + bytes-freed accounting.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import msgspec

from exlab_wizard.api.schemas import IngestJson
from exlab_wizard.cache.ingest_writer import IngestWriter
from exlab_wizard.config.models import (
    Config,
    OrchestratorConfig,
    OrchestratorStagingCleanup,
)
from exlab_wizard.constants import (
    CACHE_DIR_NAME,
    INGEST_JSON_NAME,
    INGEST_JSON_VERSION,
    IngestState,
    StagingCleanupMode,
)
from exlab_wizard.orchestrator.cleanup import (
    cleanup_eligible,
    clear_run,
    freed_bytes_and_count,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ingest(*, current_state: IngestState, history: list[dict]) -> IngestJson:
    return msgspec.convert(
        {
            "schema_version": INGEST_JSON_VERSION,
            "project_name": "Test Project",
            "equipment_id": "EQ1",
            "run_kind": "experimental",
            "run_path": "EQ1/PROJ-0001/Run_2026-04-17T14-32-00",
            "transport": "smb_mount",
            "current_state": current_state.value,
            "history": history,
        },
        type=IngestJson,
    )


def _orchestrator_config(*, mode: str, retain_hours: int = 24) -> Config:
    return Config(
        orchestrator=OrchestratorConfig(
            label="ORCH-01",
            staging_root="/staging",
            staging_cleanup=OrchestratorStagingCleanup(
                mode=mode,
                retain_hours=retain_hours,
            ),
        ),
    )


# ---------------------------------------------------------------------------
# cleanup_eligible -- manual mode
# ---------------------------------------------------------------------------


def test_cleanup_eligible_manual_mode_always_returns_false() -> None:
    config = _orchestrator_config(mode=StagingCleanupMode.MANUAL.value)
    ingest = _make_ingest(
        current_state=IngestState.SYNC_VERIFIED,
        history=[
            {"state": IngestState.SYNC_VERIFIED.value, "at": "2026-04-01T10:00:00Z"},
        ],
    )
    # Even with a sync_verified entry from years ago, manual mode never auto-clears.
    assert cleanup_eligible(ingest=ingest, config=config) is False


def test_cleanup_eligible_pre_sync_verified_states_return_false() -> None:
    config = _orchestrator_config(mode=StagingCleanupMode.SCHEDULED.value)
    for state in (
        IngestState.STAGING,
        IngestState.COMPLETE,
        IngestState.SYNC_QUEUED,
    ):
        ingest = _make_ingest(
            current_state=state, history=[{"state": state.value, "at": "2026-04-01T00:00:00Z"}]
        )
        assert cleanup_eligible(ingest=ingest, config=config) is False, state


def test_cleanup_eligible_cleared_state_returns_false() -> None:
    config = _orchestrator_config(mode=StagingCleanupMode.SCHEDULED.value)
    ingest = _make_ingest(
        current_state=IngestState.CLEARED,
        history=[{"state": IngestState.CLEARED.value, "at": "2026-04-01T00:00:00Z"}],
    )
    assert cleanup_eligible(ingest=ingest, config=config) is False


# ---------------------------------------------------------------------------
# cleanup_eligible -- scheduled mode
# ---------------------------------------------------------------------------


def test_cleanup_eligible_scheduled_within_retain_window_returns_false() -> None:
    config = _orchestrator_config(mode=StagingCleanupMode.SCHEDULED.value, retain_hours=24)
    now = datetime(2026, 4, 17, 12, 0, 0, tzinfo=UTC)
    # verified 1 hour ago -- still inside the retain window.
    verified_at = (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    ingest = _make_ingest(
        current_state=IngestState.SYNC_VERIFIED,
        history=[
            {"state": IngestState.STAGING.value, "at": "2026-04-01T00:00:00Z"},
            {"state": IngestState.COMPLETE.value, "at": "2026-04-01T01:00:00Z"},
            {"state": IngestState.SYNC_QUEUED.value, "at": "2026-04-01T02:00:00Z"},
            {"state": IngestState.SYNC_VERIFIED.value, "at": verified_at},
        ],
    )
    assert cleanup_eligible(ingest=ingest, config=config, now_utc=now) is False


def test_cleanup_eligible_scheduled_after_retain_window_returns_true() -> None:
    config = _orchestrator_config(mode=StagingCleanupMode.SCHEDULED.value, retain_hours=24)
    now = datetime(2026, 4, 17, 12, 0, 0, tzinfo=UTC)
    # verified 25 hours ago -- past the retain window.
    verified_at = (now - timedelta(hours=25)).strftime("%Y-%m-%dT%H:%M:%SZ")
    ingest = _make_ingest(
        current_state=IngestState.SYNC_VERIFIED,
        history=[
            {"state": IngestState.SYNC_VERIFIED.value, "at": verified_at},
        ],
    )
    assert cleanup_eligible(ingest=ingest, config=config, now_utc=now) is True


def test_cleanup_eligible_scheduled_at_exact_retain_boundary_returns_true() -> None:
    config = _orchestrator_config(mode=StagingCleanupMode.SCHEDULED.value, retain_hours=24)
    now = datetime(2026, 4, 17, 12, 0, 0, tzinfo=UTC)
    verified_at = (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    ingest = _make_ingest(
        current_state=IngestState.SYNC_VERIFIED,
        history=[{"state": IngestState.SYNC_VERIFIED.value, "at": verified_at}],
    )
    assert cleanup_eligible(ingest=ingest, config=config, now_utc=now) is True


def test_cleanup_eligible_returns_false_when_history_lacks_sync_verified_entry() -> None:
    config = _orchestrator_config(mode=StagingCleanupMode.SCHEDULED.value)
    # Defensive: somehow the file claims sync_verified state but no history entry.
    ingest = _make_ingest(
        current_state=IngestState.SYNC_VERIFIED,
        history=[{"state": IngestState.STAGING.value, "at": "2026-04-01T00:00:00Z"}],
    )
    assert cleanup_eligible(ingest=ingest, config=config) is False


def test_cleanup_eligible_handles_malformed_timestamp() -> None:
    config = _orchestrator_config(mode=StagingCleanupMode.SCHEDULED.value)
    ingest = _make_ingest(
        current_state=IngestState.SYNC_VERIFIED,
        history=[{"state": IngestState.SYNC_VERIFIED.value, "at": "not-an-iso-timestamp"}],
    )
    assert cleanup_eligible(ingest=ingest, config=config) is False


# ---------------------------------------------------------------------------
# clear_run
# ---------------------------------------------------------------------------


async def _seed_staged_run(tmp_path: Path) -> tuple[Path, IngestWriter, Config]:
    run_dir = tmp_path / "EQ1" / "PROJ-0001" / "Run_2026-04-17T14-32-00"
    run_dir.mkdir(parents=True)
    (run_dir / "data.bin").write_bytes(b"abcd" * 256)
    (run_dir / "more.bin").write_bytes(b"x" * 1024)
    cache_dir = run_dir / CACHE_DIR_NAME
    cache_dir.mkdir()
    writer = IngestWriter()
    payload = msgspec.convert(
        {
            "schema_version": INGEST_JSON_VERSION,
            "project_name": "Test Project",
            "equipment_id": "EQ1",
            "run_kind": "experimental",
            "run_path": "EQ1/PROJ-0001/Run_2026-04-17T14-32-00",
            "transport": "smb_mount",
            "current_state": IngestState.SYNC_VERIFIED.value,
            "history": [
                {"state": IngestState.STAGING.value, "at": "2026-04-17T14:00:00Z", "host": "h"},
                {"state": IngestState.COMPLETE.value, "at": "2026-04-17T14:30:00Z", "host": "h"},
                {"state": IngestState.SYNC_QUEUED.value, "at": "2026-04-17T14:31:00Z", "host": "h"},
                {
                    "state": IngestState.SYNC_VERIFIED.value,
                    "at": "2026-04-17T14:32:00Z",
                    "host": "h",
                },
            ],
        },
        type=IngestJson,
    )
    await writer.write_ingest(cache_dir / INGEST_JSON_NAME, payload)
    config = _orchestrator_config(mode=StagingCleanupMode.MANUAL.value)
    return run_dir, writer, config


async def test_clear_run_deletes_staging_directory_and_returns_counts(tmp_path: Path) -> None:
    run_dir, writer, config = await _seed_staged_run(tmp_path)

    files, bytes_freed = await clear_run(run_dir, config=config, ingest_writer=writer)

    assert not run_dir.exists()
    # Two data files + the ingest.json -- the cleared entry was appended
    # before the rmtree so the count picks up the on-disk file before
    # it is removed.
    assert files >= 2
    assert bytes_freed >= 1024 + 1024  # at least the two data files


async def test_clear_run_is_idempotent_when_directory_missing(tmp_path: Path) -> None:
    config = _orchestrator_config(mode=StagingCleanupMode.MANUAL.value)
    writer = IngestWriter()
    nonexistent = tmp_path / "missing" / "Run_2026-04-17T14-32-00"
    files, bytes_freed = await clear_run(
        nonexistent,
        config=config,
        ingest_writer=writer,
    )
    assert files == 0
    assert bytes_freed == 0


async def test_clear_run_skips_ingest_write_when_no_ingest_file(tmp_path: Path) -> None:
    """A defensive run without a staged ingest.json still gets deleted."""
    config = _orchestrator_config(mode=StagingCleanupMode.MANUAL.value)
    writer = IngestWriter()
    run_dir = tmp_path / "EQ1" / "PROJ-0001" / "Run_x"
    run_dir.mkdir(parents=True)
    (run_dir / "data.bin").write_bytes(b"hello")
    files, bytes_freed = await clear_run(
        run_dir,
        config=config,
        ingest_writer=writer,
    )
    assert files == 1
    assert bytes_freed == len(b"hello")
    assert not run_dir.exists()


def test_freed_bytes_and_count_returns_zero_for_missing_path(tmp_path: Path) -> None:
    files, total = freed_bytes_and_count(tmp_path / "nope")
    assert files == 0
    assert total == 0


def test_freed_bytes_and_count_includes_all_nested_files(tmp_path: Path) -> None:
    (tmp_path / "a.bin").write_bytes(b"x" * 10)
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.bin").write_bytes(b"y" * 25)
    files, total = freed_bytes_and_count(tmp_path)
    assert files == 2
    assert total == 35
