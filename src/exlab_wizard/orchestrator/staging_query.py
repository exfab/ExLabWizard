"""Read-only enumeration of runs pending NAS sync. Backend Spec §13.8.

The orchestrator exposes one read-side query that walks the configured
``staging_root``, discovers every run-leaf directory, and returns a small
DTO per run. This data backs both the bottom-dock UI panel and the
``GET /staging`` endpoint.

Per §13.2 the staging tree mirrors the final NAS layout
(``<staging_root>/<EQUIP>/<PROJ>/Runs/Run_<DATE>`` or
``<staging_root>/<EQUIP>/<PROJ>/TestRuns/TestRun_<DATE>``). Equipment id,
project name, and run kind are derived from the run path itself.

The operator-free per-file NAS sync redesign (2026-05-21) removed
``ingest.json``; a run's lifecycle ``current_state`` is the derived
``sync_state.json`` rollup -- ``syncing`` / ``synced`` / ``cleared``
(:class:`~exlab_wizard.constants.RunSyncState`), computed by
:func:`SyncStateWriter.rollup_state` from the per-run
``<run>/.exlab-wizard/sync_state.json`` record. A run with no
``sync_state.json`` yet (no sync activity) rolls up to ``syncing``.

The query returns rows sorted by directory mtime, most recent first.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from exlab_wizard.cache.sync_state_writer import SyncStateWriter
from exlab_wizard.config.models import Config
from exlab_wizard.constants import RUNS_DIR_NAME, TEST_RUNS_DIR_NAME, RunKind, RunSyncState
from exlab_wizard.logging import get_logger
from exlab_wizard.orchestrator._scan import count_files_and_bytes, walk_run_leaves
from exlab_wizard.paths import is_test_run_dir
from exlab_wizard.utils.time import dt_to_iso, parse_utc_iso_or_none, utc_now_iso, utc_now_or

__all__ = ["StagedRunSummary", "list_staged_runs"]

_log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class StagedRunSummary:
    """One row in the orchestrator's staging panel.

    Backend Spec §13.8:

    * ``path`` -- absolute filesystem path of the run leaf directory.
    * ``current_state`` -- the run's derived ``sync_state.json`` rollup
      (:class:`~exlab_wizard.constants.RunSyncState` value:
      ``"syncing"`` / ``"synced"`` / ``"cleared"``).
    * ``equipment_id`` -- the equipment segment of the run path.
    * ``project_name`` -- the LIMS project short id (parent dir).
    * ``run_kind`` -- ``"experimental"`` or ``"test"``.
    * ``file_count`` / ``byte_total`` -- size of the staged data.
    * ``elapsed_seconds_since_last_activity`` -- seconds between
      ``now_utc`` and the run directory's mtime.
    * ``last_activity_at`` -- ISO-8601 string of the directory mtime.
    """

    path: str
    current_state: str
    equipment_id: str
    project_name: str
    run_kind: str
    file_count: int
    byte_total: int
    elapsed_seconds_since_last_activity: int
    last_activity_at: str


def list_staged_runs(
    *,
    config: Config,
    staging_root: Path | None = None,
    now_utc: datetime | None = None,
    sync_state_writer: SyncStateWriter | None = None,
) -> list[StagedRunSummary]:
    """Enumerate every staged run with its derived lifecycle state.

    ``staging_root`` defaults to ``config.orchestrator.staging_root``.
    Returns an empty list when ``staging_root`` is unset / missing.

    ``current_state`` is the derived ``sync_state.json`` rollup
    (:class:`~exlab_wizard.constants.RunSyncState`): ``"syncing"`` /
    ``"synced"`` / ``"cleared"``. ``sync_state_writer`` is used to read
    each run's per-file record; a default :class:`SyncStateWriter` is
    constructed when the caller omits it. A run without a
    ``sync_state.json`` (no sync activity yet) rolls up to ``"syncing"``.

    This function is synchronous: it reads ``sync_state.json`` via the
    writer's blocking :meth:`SyncStateWriter.read_sync` so sync NiceGUI
    page handlers can call it without an event loop.

    Sort order: most recent directory mtime first.
    """
    if staging_root is not None:
        root = staging_root
    elif config.orchestrator.staging_root:
        root = Path(config.orchestrator.staging_root)
    else:
        return []
    if not root.exists():
        return []
    now = utc_now_or(now_utc)
    writer = sync_state_writer if sync_state_writer is not None else SyncStateWriter()
    rows = [_summarize_run(run_path, root, now, writer) for run_path in walk_run_leaves(root)]
    # Sort by last activity desc; ties broken by path for determinism.
    rows.sort(key=lambda s: (-_iso_to_epoch(s.last_activity_at), s.path))
    return rows


# ---------------------------------------------------------------------------
# Summary helpers
# ---------------------------------------------------------------------------


def _summarize_run(
    run_path: Path,
    staging_root: Path,
    now: datetime,
    sync_state_writer: SyncStateWriter,
) -> StagedRunSummary:
    """Build a :class:`StagedRunSummary` for ``run_path``.

    Equipment id / project name / run kind are derived from the run path
    relative to ``staging_root`` (``<EQUIP>/<PROJ>/{Runs,TestRuns}/<leaf>``).
    ``current_state`` is the derived rollup of the run's ``sync_state.json``.
    """
    equipment_id, project_name, run_kind = _path_identity(run_path, staging_root)
    file_count, byte_total = count_files_and_bytes(run_path, exclude_cache=True)
    last_activity_at = _dir_mtime_iso(run_path)
    elapsed = max(int((now - _parse_iso(last_activity_at, fallback=now)).total_seconds()), 0)
    current_state = _rollup_value(run_path, sync_state_writer)
    return StagedRunSummary(
        path=str(run_path),
        current_state=current_state,
        equipment_id=equipment_id,
        project_name=project_name,
        run_kind=run_kind,
        file_count=file_count,
        byte_total=byte_total,
        elapsed_seconds_since_last_activity=elapsed,
        last_activity_at=last_activity_at,
    )


def _rollup_value(run_path: Path, sync_state_writer: SyncStateWriter) -> str:
    """Return the derived ``sync_state.json`` rollup for ``run_path``.

    Reads the run's ``sync_state.json`` and derives the
    :class:`~exlab_wizard.constants.RunSyncState` rollup. Any read error
    (missing/locked/corrupt record) degrades to ``"syncing"`` -- the
    safe default that keeps an unproven run out of the clearable set.
    """
    try:
        state = sync_state_writer.read_sync(run_path)
    except Exception as exc:  # pragma: no cover -- defensive
        _log.warning("sync_state.json read failed for %s: %s", run_path, exc)
        return RunSyncState.SYNCING.value
    return sync_state_writer.rollup_state(state).value


def _path_identity(run_path: Path, staging_root: Path) -> tuple[str, str, str]:
    """Return ``(equipment_id, project_name, run_kind)`` from the run path.

    Falls back to empty strings / ``experimental`` when the path does not
    sit cleanly under ``staging_root`` in the expected layout.
    """
    try:
        relative = run_path.resolve().relative_to(staging_root.resolve())
    except (ValueError, OSError):
        relative = Path(run_path.name)
    parts = relative.parts
    equipment_id = parts[0] if parts else ""
    project_name = parts[1] if len(parts) >= 2 else run_path.parent.name
    # The run kind is determined by the marker folder (Runs / TestRuns) or
    # the leaf name prefix as a fallback.
    if TEST_RUNS_DIR_NAME in parts or is_test_run_dir(run_path.name):
        run_kind = RunKind.TEST.value
    elif RUNS_DIR_NAME in parts:
        run_kind = RunKind.EXPERIMENTAL.value
    else:
        run_kind = RunKind.EXPERIMENTAL.value
    return equipment_id, project_name, run_kind


def _dir_mtime_iso(run_path: Path) -> str:
    """Return the run directory mtime as a UTC ISO-8601 string."""
    try:
        mtime = run_path.stat().st_mtime
    except OSError:
        return utc_now_iso()
    return dt_to_iso(datetime.fromtimestamp(mtime, tz=UTC))


def _parse_iso(value: str, *, fallback: datetime) -> datetime:
    """Parse an ISO-8601 ``Z``-suffixed timestamp; fall back on error."""
    result = parse_utc_iso_or_none(value)
    return result if result is not None else fallback


def _iso_to_epoch(value: str) -> float:
    """Sort key helper -- returns 0.0 if ``value`` cannot be parsed."""
    result = parse_utc_iso_or_none(value)
    return result.timestamp() if result is not None else 0.0
