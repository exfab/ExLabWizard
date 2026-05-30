"""Unit tests for the folder-level sync-status rollup (Redesign §4.6)."""

from __future__ import annotations

from exlab_wizard.constants import SyncStatus
from exlab_wizard.ui.components.sync_rollup import sync_rollup

FAILED = SyncStatus.FAILED.value
BLOCKED = SyncStatus.BLOCKED_BY_VALIDATION.value
PENDING = SyncStatus.PENDING.value
SYNCED = SyncStatus.SYNCED.value
CLEANED = SyncStatus.CLEANED.value


def test_empty_input_returns_none() -> None:
    assert sync_rollup([]) is None


def test_all_none_returns_none() -> None:
    assert sync_rollup([None, None]) is None


def test_unknown_values_ignored() -> None:
    assert sync_rollup(["bogus", None]) is None


def test_single_status_rolls_up_to_itself() -> None:
    assert sync_rollup([SYNCED]) == SYNCED


def test_failed_dominates_everything() -> None:
    assert sync_rollup([SYNCED, CLEANED, PENDING, BLOCKED, FAILED]) == FAILED


def test_blocked_beats_pending_synced_cleaned() -> None:
    assert sync_rollup([CLEANED, SYNCED, PENDING, BLOCKED]) == BLOCKED


def test_pending_beats_synced_and_cleaned() -> None:
    assert sync_rollup([CLEANED, SYNCED, PENDING]) == PENDING


def test_synced_beats_cleaned() -> None:
    assert sync_rollup([CLEANED, SYNCED]) == SYNCED


def test_cleaned_is_lowest() -> None:
    assert sync_rollup([CLEANED, CLEANED]) == CLEANED


def test_none_and_unknown_excluded_but_known_survives() -> None:
    assert sync_rollup([None, "bogus", SYNCED, None]) == SYNCED


def test_full_severity_order() -> None:
    """failed > blocked > pending > synced > cleaned: each prefix's rollup is
    its first (highest-severity) element."""
    order = [FAILED, BLOCKED, PENDING, SYNCED, CLEANED]
    for i in range(len(order)):
        assert sync_rollup(order[i:]) == order[i]


def test_accepts_syncstatus_enum_members_directly() -> None:
    """SyncStatus is a StrEnum, so passing members (not .value strings) works.

    Guards the contract that a future caller can feed enum instances from
    ``FileListEntry.sync_status`` without first calling ``.value``.
    """
    assert sync_rollup([SyncStatus.SYNCED, SyncStatus.FAILED]) == FAILED
    assert sync_rollup([SyncStatus.CLEANED, "failed"]) == FAILED
