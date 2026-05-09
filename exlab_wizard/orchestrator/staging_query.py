"""Read-only enumeration of staged runs. Backend Spec §13.8.

The orchestrator exposes one read-side query that walks the configured
``staging_root``, opens each run's ``ingest.json``, and returns a small
DTO per run. This data backs both the bottom-dock UI panel and the
``GET /staging`` endpoint.

Per §13.2 the staging tree mirrors the final NAS layout
(``<staging_root>/<EQUIP>/<PROJ>/Run_<DATE>`` or
``<staging_root>/<EQUIP>/<PROJ>/TestRuns/TestRun_<DATE>``). The walker
descends to the run-leaf directory, looks for ``.exlab-wizard/ingest.json``,
and skips any directory that lacks one (the staging push has not yet
written the initial state record).

The query returns rows sorted by "most recent activity first" -- defined
as the timestamp of the most recent ``history`` entry. Runs without a
parsable history fall back to the directory's mtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import msgspec

from exlab_wizard.api.schemas import IngestJson
from exlab_wizard.config.models import Config
from exlab_wizard.io import read_msgspec_json
from exlab_wizard.logging import get_logger
from exlab_wizard.orchestrator._scan import count_files_and_bytes, walk_run_leaves
from exlab_wizard.paths import ingest_json_path
from exlab_wizard.utils.time import (
    dt_to_iso,
    parse_utc_iso_or_none,
    utc_now_iso,
    utc_now_or,
)

__all__ = ["StagedRunSummary", "list_staged_runs"]

_log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class StagedRunSummary:
    """One row in the orchestrator's staging panel.

    Backend Spec §13.8:

    * ``path`` -- absolute filesystem path of the run leaf directory.
    * ``current_state`` -- the latest ``ingest.json`` ``current_state``.
    * ``equipment_id`` -- the equipment segment of the run path.
    * ``project_name`` -- the LIMS project short id (parent dir).
    * ``run_kind`` -- ``"experimental"`` or ``"test"``.
    * ``file_count`` / ``byte_total`` -- size of the staged data.
    * ``elapsed_seconds_since_last_activity`` -- seconds between
      ``now_utc`` and the most recent history entry's ``at`` field
      (falls back to the directory mtime when no history exists).
    * ``last_activity_at`` -- ISO-8601 string of the same timestamp.
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
) -> list[StagedRunSummary]:
    """Enumerate every staged run with its current lifecycle state.

    ``staging_root`` defaults to ``config.orchestrator.staging_root``.
    Returns an empty list when the orchestrator is not enabled (cheap
    no-op so callers never need to gate on the flag themselves) or when
    the directory does not exist.

    Sort order: most recent activity first.
    """
    if not config.orchestrator.enabled:
        return []
    root = staging_root if staging_root is not None else Path(config.orchestrator.staging_root)
    if not root.exists():
        return []
    now = utc_now_or(now_utc)
    rows = [
        summary
        for run_path in walk_run_leaves(root)
        if (summary := _summarize_run(run_path, now)) is not None
    ]
    # Sort by last activity desc; ties broken by path for determinism.
    rows.sort(key=lambda s: (-_iso_to_epoch(s.last_activity_at), s.path))
    return rows


# ---------------------------------------------------------------------------
# Summary helpers
# ---------------------------------------------------------------------------


def _summarize_run(run_path: Path, now: datetime) -> StagedRunSummary | None:
    """Build a :class:`StagedRunSummary` for ``run_path``, or None to skip.

    A run is included only if its ``.exlab-wizard/ingest.json`` exists and
    decodes successfully. Other shapes (a run that's mid-push and hasn't
    written its initial ingest yet) are silently omitted -- the watcher
    will catch up on the next pass.
    """
    ingest_path = ingest_json_path(run_path)
    if not ingest_path.exists():
        return None
    try:
        ingest = read_msgspec_json(ingest_path, IngestJson)
    except (msgspec.DecodeError, msgspec.ValidationError) as exc:
        _log.warning("ingest.json at %s could not be decoded: %s", ingest_path, exc)
        return None

    file_count, byte_total = count_files_and_bytes(run_path, exclude_cache=True)
    last_activity_at = _last_activity_at(ingest, run_path)
    elapsed = max(int((now - _parse_iso(last_activity_at, fallback=now)).total_seconds()), 0)
    project_name = ingest.project_name or run_path.parent.name
    return StagedRunSummary(
        path=str(run_path),
        current_state=ingest.current_state,
        equipment_id=ingest.equipment_id,
        project_name=project_name,
        run_kind=ingest.run_kind,
        file_count=file_count,
        byte_total=byte_total,
        elapsed_seconds_since_last_activity=elapsed,
        last_activity_at=last_activity_at,
    )


def _last_activity_at(ingest: IngestJson, run_path: Path) -> str:
    """Return the ISO timestamp of the most recent activity.

    Preference order:

    1. Most recent ``history`` entry's ``at`` field.
    2. The directory's mtime, formatted as UTC ISO.
    """
    if ingest.history:
        last = ingest.history[-1]
        at = last.get("at")
        if isinstance(at, str) and at:
            return at
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
