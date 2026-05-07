"""Unit tests for ``exlab_wizard.orchestrator.staging_query``.

Backend Spec §13.8. Verify the walker discovers run leaves, decodes
``ingest.json``, and returns rows sorted by last activity (most recent
first).
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
)
from exlab_wizard.orchestrator.staging_query import (
    StagedRunSummary,
    list_staged_runs,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _config(staging_root: Path, *, enabled: bool = True) -> Config:
    return Config(
        orchestrator=OrchestratorConfig(
            enabled=enabled,
            label="ORCH",
            staging_root=str(staging_root),
            staging_cleanup=OrchestratorStagingCleanup(),
        ),
    )


async def _seed_run(
    staging_root: Path,
    *,
    equipment: str = "EQ1",
    project: str = "PROJ-0001",
    run_name: str = "Run_2026-04-17T14-32-00",
    test_run: bool = False,
    state: IngestState = IngestState.STAGING,
    last_at: str = "2026-04-17T14:30:00Z",
    extra_files: tuple[tuple[str, bytes], ...] = (("data.bin", b"abcd" * 256),),
) -> Path:
    if test_run:
        run_dir = staging_root / equipment / project / "TestRuns" / run_name
    else:
        run_dir = staging_root / equipment / project / run_name
    run_dir.mkdir(parents=True)
    for fname, fdata in extra_files:
        (run_dir / fname).write_bytes(fdata)
    cache_dir = run_dir / CACHE_DIR_NAME
    cache_dir.mkdir()
    payload = msgspec.convert(
        {
            "schema_version": INGEST_JSON_VERSION,
            "project_name": project,
            "equipment_id": equipment,
            "run_kind": "test" if test_run else "experimental",
            "run_path": str(run_dir),
            "transport": "smb_mount",
            "current_state": state.value,
            "history": [
                {"state": state.value, "at": last_at, "host": "host"},
            ],
        },
        type=IngestJson,
    )
    writer = IngestWriter()
    await writer.write_ingest(cache_dir / INGEST_JSON_NAME, payload)
    return run_dir


# ---------------------------------------------------------------------------
# Disabled / missing-directory cases
# ---------------------------------------------------------------------------


def test_list_staged_runs_returns_empty_when_orchestrator_disabled(tmp_path: Path) -> None:
    config = _config(tmp_path, enabled=False)
    assert list_staged_runs(config=config) == []


def test_list_staged_runs_returns_empty_when_staging_root_missing(tmp_path: Path) -> None:
    config = _config(tmp_path / "missing")
    assert list_staged_runs(config=config) == []


def test_list_staged_runs_skips_runs_without_ingest_json(tmp_path: Path) -> None:
    # Stage a run without writing ingest.json (mid-push).
    run_dir = tmp_path / "EQ1" / "PROJ-0001" / "Run_2026-04-17T14-32-00"
    run_dir.mkdir(parents=True)
    (run_dir / "data.bin").write_bytes(b"x")
    config = _config(tmp_path)
    assert list_staged_runs(config=config) == []


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_list_staged_runs_returns_summary_rows(tmp_path: Path) -> None:
    await _seed_run(tmp_path)
    config = _config(tmp_path)

    rows = list_staged_runs(config=config)

    assert len(rows) == 1
    row = rows[0]
    assert isinstance(row, StagedRunSummary)
    assert row.current_state == IngestState.STAGING.value
    assert row.equipment_id == "EQ1"
    assert row.project_name == "PROJ-0001"
    assert row.run_kind == "experimental"
    assert row.file_count == 1
    assert row.byte_total == 4 * 256


async def test_list_staged_runs_includes_test_runs_under_TestRuns(tmp_path: Path) -> None:
    await _seed_run(
        tmp_path,
        run_name="TestRun_2026-04-17T09-12-00",
        test_run=True,
    )
    config = _config(tmp_path)

    rows = list_staged_runs(config=config)
    assert len(rows) == 1
    assert rows[0].run_kind == "test"


async def test_list_staged_runs_sorts_most_recent_first(tmp_path: Path) -> None:
    await _seed_run(
        tmp_path,
        run_name="Run_2026-04-15T00-00-00",
        last_at="2026-04-15T00:00:00Z",
    )
    await _seed_run(
        tmp_path,
        project="PROJ-0002",
        run_name="Run_2026-04-17T00-00-00",
        last_at="2026-04-17T00:00:00Z",
    )
    await _seed_run(
        tmp_path,
        project="PROJ-0003",
        run_name="Run_2026-04-16T00-00-00",
        last_at="2026-04-16T00:00:00Z",
    )

    rows = list_staged_runs(config=_config(tmp_path))

    assert [r.last_activity_at for r in rows] == [
        "2026-04-17T00:00:00Z",
        "2026-04-16T00:00:00Z",
        "2026-04-15T00:00:00Z",
    ]


async def test_list_staged_runs_computes_elapsed_seconds(tmp_path: Path) -> None:
    last_at = "2026-04-17T12:00:00Z"
    await _seed_run(tmp_path, last_at=last_at)
    config = _config(tmp_path)
    now = datetime(2026, 4, 17, 12, 30, 0, tzinfo=UTC)

    rows = list_staged_runs(config=config, now_utc=now)

    assert rows[0].elapsed_seconds_since_last_activity == int(timedelta(minutes=30).total_seconds())


async def test_list_staged_runs_skips_unparsable_ingest_json(tmp_path: Path) -> None:
    run_dir = tmp_path / "EQ1" / "PROJ-0001" / "Run_2026-04-17T14-32-00"
    run_dir.mkdir(parents=True)
    cache = run_dir / CACHE_DIR_NAME
    cache.mkdir()
    (cache / INGEST_JSON_NAME).write_bytes(b"not json {{{")
    config = _config(tmp_path)
    rows = list_staged_runs(config=config)
    assert rows == []


async def test_list_staged_runs_excludes_cache_dir_from_byte_total(tmp_path: Path) -> None:
    """The .exlab-wizard subtree is metadata, not staged data."""
    run_dir = await _seed_run(tmp_path)
    # Add a junk file inside .exlab-wizard to ensure it's NOT counted.
    (run_dir / CACHE_DIR_NAME / "junk.bin").write_bytes(b"a" * 1000)
    rows = list_staged_runs(config=_config(tmp_path))
    assert rows[0].byte_total == 4 * 256  # only the data.bin


async def test_list_staged_runs_uses_explicit_staging_root_param(tmp_path: Path) -> None:
    other_root = tmp_path / "other"
    await _seed_run(other_root)
    # Config points at a different directory; explicit staging_root overrides.
    config = _config(tmp_path / "ignored")
    rows = list_staged_runs(config=config, staging_root=other_root)
    assert len(rows) == 1


async def test_list_staged_runs_falls_back_to_mtime_when_history_empty(
    tmp_path: Path,
) -> None:
    """An ingest.json with empty history uses the directory mtime as fallback."""
    run_dir = tmp_path / "EQ1" / "PROJ-0001" / "Run_2026-04-17T14-32-00"
    run_dir.mkdir(parents=True)
    (run_dir / "data.bin").write_bytes(b"x")
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
            "current_state": IngestState.STAGING.value,
            "history": [],  # explicit empty history
        },
        type=IngestJson,
    )
    await IngestWriter().write_ingest(cache / INGEST_JSON_NAME, payload)
    rows = list_staged_runs(config=_config(tmp_path))
    assert len(rows) == 1
    # The mtime-derived ISO string is non-empty.
    assert rows[0].last_activity_at


async def test_list_staged_runs_handles_history_entry_without_at_field(
    tmp_path: Path,
) -> None:
    """Defensive: a malformed history entry with no ``at`` falls back to mtime."""
    run_dir = tmp_path / "EQ1" / "PROJ-0001" / "Run_2026-04-17T14-32-00"
    run_dir.mkdir(parents=True)
    (run_dir / "data.bin").write_bytes(b"x")
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
            "current_state": IngestState.STAGING.value,
            "history": [{"state": IngestState.STAGING.value, "host": "host"}],  # no ``at``
        },
        type=IngestJson,
    )
    await IngestWriter().write_ingest(cache / INGEST_JSON_NAME, payload)
    rows = list_staged_runs(config=_config(tmp_path))
    assert rows[0].last_activity_at  # something fell-through
