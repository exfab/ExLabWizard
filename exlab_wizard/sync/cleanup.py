"""Cleanup safety interlocks. Backend Spec §7.1.6.

The cleanup reaper deletes local files only when **all** of the
following hold for a job:

1. ``verify_passes >= nas_cleanup.min_verify_passes`` (default 2).
2. Hours since the most recent ``verified_at`` >= ``min_age_hours``
   (default 24).
3. The remote NAS path is reachable (the caller passes the result of
   a remote ``stat`` as ``remote_stat_ok``).
4. No active ``validation_overrides`` revocation (tombstone) has been
   written within the last ``min_age_hours`` -- a revoked override
   re-blocks sync, so we don't want to delete locally if the run is
   now blocked.

This module is the pure interlock evaluator. The reaper itself lives in
:mod:`exlab_wizard.sync.nas_client`; it consults this helper before
issuing a delete.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from exlab_wizard.config.models import NASCleanupConfig
from exlab_wizard.logging import get_logger
from exlab_wizard.sync.queue import SyncJobRow
from exlab_wizard.utils.time import parse_utc_iso_or_none

__all__ = [
    "cleanup_interlocks_satisfied",
    "has_recent_revocation",
]

_log = get_logger(__name__)


def has_recent_revocation(
    overrides: list[dict[str, Any]],
    *,
    now_utc: datetime,
    min_age_hours: int,
) -> bool:
    """Return True if any tombstone was recorded within ``min_age_hours``.

    A tombstone is an override-list entry with ``revoked: True``. The
    timestamp is read from ``recorded_at``; entries with a missing or
    malformed timestamp are treated as recent (fail-safe: we'd rather
    block cleanup than delete files for a run that may be re-gated).
    """
    cutoff = now_utc - timedelta(hours=min_age_hours)
    for entry in overrides:
        if not entry.get("revoked", False):
            continue
        recorded = parse_utc_iso_or_none(entry.get("recorded_at"))
        if recorded is None:
            # Malformed timestamp: be conservative.
            return True
        if recorded > cutoff:
            return True
    return False


def cleanup_interlocks_satisfied(
    *,
    job: SyncJobRow,
    run_path: Any,
    now_utc: datetime,
    config: NASCleanupConfig,
    overrides_active: list[dict[str, Any]],
    remote_stat_ok: bool,
) -> bool:
    """Evaluate every §7.1.6 interlock; return True iff all pass.

    Logs a debug entry naming the failing interlock when one fails so
    the operator can see why a job stayed in ``CLEANUP_ELIGIBLE``.

    The ``run_path`` parameter is accepted (but currently unused) so
    callers can pass the run directory through unchanged; future
    interlocks (e.g., size-on-disk threshold) may consult it.
    """
    # 1. verify_passes threshold.
    if job.verify_passes < config.min_verify_passes:
        _log.debug(
            "cleanup blocked: verify_passes=%d < min=%d for job %s",
            job.verify_passes,
            config.min_verify_passes,
            job.id,
        )
        return False

    # 2. min_age_hours since verified_at.
    verified_dt = parse_utc_iso_or_none(job.verified_at)
    if verified_dt is None:
        _log.debug("cleanup blocked: verified_at missing/malformed for job %s", job.id)
        return False
    age = now_utc - verified_dt
    if age < timedelta(hours=config.min_age_hours):
        _log.debug(
            "cleanup blocked: age=%s < min_age=%dh for job %s",
            age,
            config.min_age_hours,
            job.id,
        )
        return False

    # 3. remote NAS reachable.
    if not remote_stat_ok:
        _log.debug("cleanup blocked: remote stat failed for job %s", job.id)
        return False

    # 4. no recent revocation.
    if has_recent_revocation(
        overrides_active,
        now_utc=now_utc,
        min_age_hours=config.min_age_hours,
    ):
        _log.debug("cleanup blocked: recent revocation for job %s", job.id)
        return False

    return True
