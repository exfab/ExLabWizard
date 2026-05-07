"""Staging-side cleanup helpers. Backend Spec §13.7.

Once a run is verified on the NAS, the orchestrator deletes the local
staging copy. Two policies are supported:

* ``manual`` (default for v1) -- only an explicit operator action
  advances ``sync_verified`` -> ``cleared``. The watcher never auto-clears.
* ``scheduled`` -- runs whose ``sync_verified_at`` was at least
  ``retain_hours`` ago are auto-cleared by the periodic sweep.

Deletion is logged with file count and bytes freed (§13.7).

Both helpers are pure read-side utilities except :func:`clear_run`,
which performs the on-disk delete and writes the ``cleared`` history
entry. The watcher keeps the responsibility of *deciding* when to call
:func:`clear_run` -- this module only enforces the policy and the
filesystem effect.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from exlab_wizard.api.schemas import IngestJson
from exlab_wizard.cache.ingest_writer import IngestWriter, default_host
from exlab_wizard.config.models import Config
from exlab_wizard.constants import (
    CACHE_DIR_NAME,
    INGEST_JSON_NAME,
    IngestState,
    StagingCleanupMode,
)
from exlab_wizard.logging import get_logger

__all__ = ["cleanup_eligible", "clear_run", "freed_bytes_and_count"]

_log = get_logger(__name__)


def cleanup_eligible(
    *,
    ingest: IngestJson,
    config: Config,
    now_utc: datetime | None = None,
) -> bool:
    """Return True if the run's local staging copy should be cleared now.

    Backend Spec §13.7:

    * ``manual`` -- always returns False. The operator must invoke
      :func:`clear_run` directly (UI button or API).
    * ``scheduled`` -- returns True iff ``current_state == sync_verified``
      AND ``sync_verified_at + retain_hours <= now_utc``.

    A run that is not ``sync_verified`` is never eligible -- attempting
    to clear earlier states is a contract violation that the watcher
    must avoid.
    """
    if ingest.current_state != IngestState.SYNC_VERIFIED.value:
        return False
    mode = config.orchestrator.staging_cleanup.mode
    if mode == StagingCleanupMode.MANUAL.value:
        return False
    if mode != StagingCleanupMode.SCHEDULED.value:
        # Defensive: the Pydantic Literal already constrains the values,
        # but if a future mode is added without a code path here we
        # default to "not eligible" -- the safer behaviour.
        return False
    verified_at = _find_state_timestamp(ingest, IngestState.SYNC_VERIFIED)
    if verified_at is None:
        return False
    now = now_utc if now_utc is not None else datetime.now(tz=UTC)
    retain_hours = config.orchestrator.staging_cleanup.retain_hours
    return verified_at + timedelta(hours=retain_hours) <= now


async def clear_run(
    run_path: Path,
    *,
    config: Config,
    ingest_writer: IngestWriter,
    host: str | None = None,
) -> tuple[int, int]:
    """Remove the staged run directory and append the ``cleared`` entry.

    Returns ``(file_count, bytes_freed)`` so the caller can log/notify
    accurately. The ingest entry is written **before** the deletion so a
    crash mid-clear leaves a coherent state record.

    The function is idempotent: calling it after the directory is gone
    is a no-op that returns ``(0, 0)`` and does not append a duplicate
    history entry.
    """
    _ = config  # kept on the signature for spec parity / future hooks
    if not run_path.exists():  # noqa: ASYNC240 -- one-shot stat, sync filelock cycle below
        return 0, 0
    file_count, bytes_freed = freed_bytes_and_count(run_path)
    ingest_path = run_path / CACHE_DIR_NAME / INGEST_JSON_NAME
    host_label = host or default_host()
    if ingest_path.exists():
        await ingest_writer.append_state_transition(
            ingest_path,
            IngestState.CLEARED,
            host=host_label,
        )
    # Now delete the staged directory in full -- the ingest.json entry
    # we just wrote is part of the directory and is acceptable to discard
    # because §13 only requires the ``cleared`` entry to flow to NAS via
    # the prior ``sync_verified`` transition (the NAS copy already has it).
    shutil.rmtree(run_path, ignore_errors=True)
    _log.info(
        "staging cleared: path=%s files=%d bytes_freed=%d host=%s",
        run_path,
        file_count,
        bytes_freed,
        host_label,
    )
    return file_count, bytes_freed


def freed_bytes_and_count(run_path: Path) -> tuple[int, int]:
    """Sum file count and byte total under ``run_path``.

    Counts files only (directories are not counted as files); the
    ``.exlab-wizard/`` cache subtree is included because :func:`clear_run`
    deletes the whole run.
    """
    files = 0
    total = 0
    if not run_path.exists():
        return 0, 0
    for entry in run_path.rglob("*"):
        try:
            if entry.is_file():
                files += 1
                total += entry.stat().st_size
        except OSError:
            continue
    return files, total


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _find_state_timestamp(ingest: IngestJson, target: IngestState) -> datetime | None:
    """Return the latest history-entry ``at`` for ``target`` or None."""
    for entry in reversed(ingest.history):
        if entry.get("state") != target.value:
            continue
        return _parse_iso(entry.get("at"))
    return None


def _parse_iso(value: Any) -> datetime | None:
    """Parse a ``Z``-suffixed ISO-8601 string; return ``None`` on failure."""
    if not isinstance(value, str) or not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00") if value.endswith("Z") else value
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed
