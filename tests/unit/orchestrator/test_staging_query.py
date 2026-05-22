"""Unit tests for ``exlab_wizard.orchestrator.staging_query``.

Backend Spec §13.8. The operator-free per-file NAS sync redesign
(2026-05-21) removed ``ingest.json``; the query discovers run leaves,
derives identity from the run path, and reports ``current_state`` as the
derived ``sync_state.json`` rollup (``syncing`` / ``synced`` / ``cleared``).
Rows are sorted by directory mtime, most recent first.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from exlab_wizard.cache.sync_state_writer import SyncStateWriter
from exlab_wizard.config.models import (
    Config,
    OrchestratorConfig,
    OrchestratorStagingCleanup,
)
from exlab_wizard.constants import RUNS_DIR_NAME, TEST_RUNS_DIR_NAME
from exlab_wizard.orchestrator.staging_query import StagedRunSummary, list_staged_runs

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _config(staging_root: Path) -> Config:
    return Config(
        orchestrator=OrchestratorConfig(
            label="ORCH",
            staging_root=str(staging_root),
            staging_cleanup=OrchestratorStagingCleanup(),
        ),
    )


def _seed_run(
    staging_root: Path,
    *,
    equipment: str = "EQ1",
    project: str = "PROJ-0001",
    run_name: str = "Run_2026-04-17T14-32-00",
    test_run: bool = False,
    extra_files: tuple[tuple[str, bytes], ...] = (("data.bin", b"abcd" * 256),),
) -> Path:
    marker = TEST_RUNS_DIR_NAME if test_run else RUNS_DIR_NAME
    run_dir = staging_root / equipment / project / marker / run_name
    run_dir.mkdir(parents=True)
    for fname, fdata in extra_files:
        (run_dir / fname).write_bytes(fdata)
    return run_dir


# ---------------------------------------------------------------------------
# Missing-directory cases
# ---------------------------------------------------------------------------


def test_list_staged_runs_returns_empty_when_staging_root_unset() -> None:
    config = Config(orchestrator=OrchestratorConfig(label="ORCH", staging_root=""))
    assert list_staged_runs(config=config) == []


def test_list_staged_runs_returns_empty_when_staging_root_missing(tmp_path: Path) -> None:
    config = _config(tmp_path / "missing")
    assert list_staged_runs(config=config) == []


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_list_staged_runs_returns_summary_rows(tmp_path: Path) -> None:
    _seed_run(tmp_path)
    rows = list_staged_runs(config=_config(tmp_path))

    assert len(rows) == 1
    row = rows[0]
    assert isinstance(row, StagedRunSummary)
    # No sync_state.json yet -> rolls up to "syncing".
    assert row.current_state == "syncing"
    assert row.equipment_id == "EQ1"
    assert row.project_name == "PROJ-0001"
    assert row.run_kind == "experimental"
    assert row.file_count == 1
    assert row.byte_total == 4 * 256


def test_list_staged_runs_includes_test_runs_under_TestRuns(tmp_path: Path) -> None:
    _seed_run(tmp_path, run_name="TestRun_2026-04-17T09-12-00", test_run=True)
    rows = list_staged_runs(config=_config(tmp_path))
    assert len(rows) == 1
    assert rows[0].run_kind == "test"


def test_list_staged_runs_reports_syncing_when_a_file_is_unverified(tmp_path: Path) -> None:
    """A run with an unverified tracked file rolls up to ``syncing``."""
    run_dir = _seed_run(tmp_path)
    writer = SyncStateWriter()
    asyncio.run(writer.upsert_file(run_dir, "data.bin", synced_signature=None, verified_at=None))
    rows = list_staged_runs(config=_config(tmp_path))
    assert rows[0].current_state == "syncing"


def test_list_staged_runs_reports_synced_when_all_files_verified(tmp_path: Path) -> None:
    """Every tracked file verified -> ``current_state == 'synced'``."""
    run_dir = _seed_run(tmp_path)
    writer = SyncStateWriter()
    asyncio.run(
        writer.upsert_file(
            run_dir, "data.bin", synced_signature=(1024, 111), verified_at="2026-05-21T00:00:00Z"
        )
    )
    rows = list_staged_runs(config=_config(tmp_path))
    assert rows[0].current_state == "synced"


def test_list_staged_runs_reports_cleared_after_mark_cleared(tmp_path: Path) -> None:
    """A run whose ``sync_state.json`` carries ``cleared_at`` rolls up to ``cleared``."""
    run_dir = _seed_run(tmp_path)
    writer = SyncStateWriter()
    asyncio.run(
        writer.upsert_file(
            run_dir, "data.bin", synced_signature=(1024, 111), verified_at="2026-05-21T00:00:00Z"
        )
    )
    asyncio.run(writer.mark_cleared(run_dir))
    rows = list_staged_runs(config=_config(tmp_path))
    assert rows[0].current_state == "cleared"


def test_list_staged_runs_remodified_file_flips_rollup_back_to_syncing(tmp_path: Path) -> None:
    """A re-modified file (signature cleared -> unverified) flips synced back to syncing."""
    run_dir = _seed_run(tmp_path)
    writer = SyncStateWriter()
    # First fully verified.
    asyncio.run(
        writer.upsert_file(
            run_dir, "data.bin", synced_signature=(1024, 111), verified_at="2026-05-21T00:00:00Z"
        )
    )
    assert list_staged_runs(config=_config(tmp_path))[0].current_state == "synced"
    # The file is re-modified: the poller clears the verify mark for the
    # re-eligible file, dropping the run rollup back to ``syncing``.
    asyncio.run(writer.upsert_file(run_dir, "data.bin", synced_signature=None, verified_at=None))
    assert list_staged_runs(config=_config(tmp_path))[0].current_state == "syncing"


def test_list_staged_runs_includes_runs_without_creation_metadata(tmp_path: Path) -> None:
    """A run leaf with no cache metadata is still listed (ingest.json gone)."""
    run_dir = tmp_path / "EQ1" / "PROJ-0001" / RUNS_DIR_NAME / "Run_2026-04-17T14-32-00"
    run_dir.mkdir(parents=True)
    (run_dir / "data.bin").write_bytes(b"x")
    rows = list_staged_runs(config=_config(tmp_path))
    assert len(rows) == 1
    assert rows[0].equipment_id == "EQ1"


def test_list_staged_runs_sorts_most_recent_first(tmp_path: Path) -> None:
    import os
    import time

    older = _seed_run(tmp_path, run_name="Run_2026-04-15T00-00-00")
    newer = _seed_run(tmp_path, project="PROJ-0002", run_name="Run_2026-04-17T00-00-00")
    # Force a deterministic mtime ordering.
    base = time.time()
    os.utime(older, (base - 200, base - 200))
    os.utime(newer, (base, base))

    rows = list_staged_runs(config=_config(tmp_path))
    assert [r.path for r in rows] == [str(newer), str(older)]


def test_list_staged_runs_excludes_cache_dir_from_byte_total(tmp_path: Path) -> None:
    """The .exlab-wizard subtree is metadata, not staged data."""
    from exlab_wizard.constants import CACHE_DIR_NAME

    run_dir = _seed_run(tmp_path)
    cache = run_dir / CACHE_DIR_NAME
    cache.mkdir()
    (cache / "junk.bin").write_bytes(b"a" * 1000)
    rows = list_staged_runs(config=_config(tmp_path))
    assert rows[0].byte_total == 4 * 256  # only the data.bin


def test_list_staged_runs_uses_explicit_staging_root_param(tmp_path: Path) -> None:
    other_root = tmp_path / "other"
    _seed_run(other_root)
    config = _config(tmp_path / "ignored")
    rows = list_staged_runs(config=config, staging_root=other_root)
    assert len(rows) == 1


def test_list_staged_runs_sets_last_activity_from_directory_mtime(tmp_path: Path) -> None:
    _seed_run(tmp_path)
    rows = list_staged_runs(config=_config(tmp_path))
    # The mtime-derived ISO string is non-empty.
    assert rows[0].last_activity_at
    assert rows[0].elapsed_seconds_since_last_activity >= 0
