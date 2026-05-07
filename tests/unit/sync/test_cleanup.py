"""Tests for ``exlab_wizard.sync.cleanup``.

Backend Spec §7.1.6. Each interlock must independently block cleanup;
the all-pass case returns True.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from exlab_wizard.config.models import NASCleanupConfig
from exlab_wizard.sync.cleanup import (
    cleanup_interlocks_satisfied,
    has_recent_revocation,
)
from exlab_wizard.sync.queue import SyncJobRow, SyncJobState

_NOW = datetime(2026, 4, 17, 14, 0, 0, tzinfo=UTC)


def _job(
    *,
    verify_passes: int = 2,
    verified_at: str | None = "2026-04-15T14:00:00Z",
) -> SyncJobRow:
    return SyncJobRow(
        id="j1",
        run_path="/x/y/run",
        equipment_id="EQ1",
        state=SyncJobState.VERIFIED,
        verify_passes=verify_passes,
        verified_at=verified_at,
        enqueued_at="2026-04-14T00:00:00Z",
    )


def _config(
    *,
    enabled: bool = True,
    min_verify_passes: int = 2,
    min_age_hours: int = 24,
    retain_cache: bool = True,
) -> NASCleanupConfig:
    return NASCleanupConfig(
        enabled=enabled,
        min_verify_passes=min_verify_passes,
        min_age_hours=min_age_hours,
        retain_cache=retain_cache,
    )


def test_all_interlocks_pass(tmp_path: Path) -> None:
    """Happy path: cleanup is allowed."""
    assert cleanup_interlocks_satisfied(
        job=_job(),
        run_path=tmp_path,
        now_utc=_NOW,
        config=_config(),
        overrides_active=[],
        remote_stat_ok=True,
    )


def test_min_verify_passes_blocks(tmp_path: Path) -> None:
    """Failing the verify-passes threshold blocks cleanup."""
    assert not cleanup_interlocks_satisfied(
        job=_job(verify_passes=1),
        run_path=tmp_path,
        now_utc=_NOW,
        config=_config(min_verify_passes=2),
        overrides_active=[],
        remote_stat_ok=True,
    )


def test_min_age_hours_blocks(tmp_path: Path) -> None:
    """A verify that happened too recently blocks cleanup."""
    recent_iso = (_NOW - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert not cleanup_interlocks_satisfied(
        job=_job(verified_at=recent_iso),
        run_path=tmp_path,
        now_utc=_NOW,
        config=_config(min_age_hours=24),
        overrides_active=[],
        remote_stat_ok=True,
    )


def test_remote_stat_failure_blocks(tmp_path: Path) -> None:
    """An unreachable NAS blocks cleanup."""
    assert not cleanup_interlocks_satisfied(
        job=_job(),
        run_path=tmp_path,
        now_utc=_NOW,
        config=_config(),
        overrides_active=[],
        remote_stat_ok=False,
    )


def test_recent_revocation_blocks(tmp_path: Path) -> None:
    """A tombstone written within ``min_age_hours`` blocks cleanup."""
    overrides = [
        {
            "id": "t1",
            "revoked": True,
            "revokes": "o1",
            "operator": "alice",
            "recorded_at": (_NOW - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "reason": "manual",
        }
    ]
    assert not cleanup_interlocks_satisfied(
        job=_job(),
        run_path=tmp_path,
        now_utc=_NOW,
        config=_config(min_age_hours=24),
        overrides_active=overrides,
        remote_stat_ok=True,
    )


def test_old_revocation_does_not_block(tmp_path: Path) -> None:
    overrides = [
        {
            "id": "t1",
            "revoked": True,
            "revokes": "o1",
            "operator": "alice",
            "recorded_at": (_NOW - timedelta(hours=200)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "reason": "manual",
        }
    ]
    assert cleanup_interlocks_satisfied(
        job=_job(),
        run_path=tmp_path,
        now_utc=_NOW,
        config=_config(min_age_hours=24),
        overrides_active=overrides,
        remote_stat_ok=True,
    )


def test_missing_verified_at_blocks(tmp_path: Path) -> None:
    """A verified_at column missing or malformed blocks cleanup."""
    assert not cleanup_interlocks_satisfied(
        job=_job(verified_at=None),
        run_path=tmp_path,
        now_utc=_NOW,
        config=_config(),
        overrides_active=[],
        remote_stat_ok=True,
    )


def test_has_recent_revocation_with_malformed_timestamp_is_recent() -> None:
    """A malformed ``recorded_at`` is treated as recent (fail-safe)."""
    overrides = [
        {
            "id": "t1",
            "revoked": True,
            "revokes": "o1",
            "operator": "x",
            "recorded_at": "not-iso",
            "reason": "x",
        }
    ]
    assert has_recent_revocation(overrides, now_utc=_NOW, min_age_hours=24)


def test_has_recent_revocation_ignores_non_tombstones() -> None:
    """An override entry without ``revoked: True`` is ignored."""
    overrides = [
        {
            "id": "o1",
            "problem_class": "x",
            "operator": "x",
            "recorded_at": (_NOW - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "reason": "x",
            "revoked": False,
        }
    ]
    assert not has_recent_revocation(overrides, now_utc=_NOW, min_age_hours=24)
