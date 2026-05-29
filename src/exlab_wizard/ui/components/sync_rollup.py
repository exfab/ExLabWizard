"""Folder-level sync-status rollup (GUI/Orchestrator Redesign §4.6).

When a folder is selected in the centre file list, the metadata sub-card
shows a single "worst-of" rollup of its children's per-file sync states.
This module owns that reduction as a pure, NiceGUI-free function so the
ordering is testable in isolation.

**Why a UI-only ordering.** The per-file states are :class:`SyncStatus`
values (``pending`` / ``synced`` / ``cleaned`` / ``failed`` /
``blocked_by_validation``). ``SyncStatus`` is a schema-committed
``StrEnum`` that deliberately carries *no* severity order -- the enum
module forbids reordering without a coordinated schema-version bump
(constants/enums.py). The rollup is a presentation concern, so the
severity ranking lives here, beside its only consumer, rather than on the
enum. If a second consumer ever needs the same order, promoting it onto
``SyncStatus`` becomes a separate, coordinated change (spec §11).
"""

from __future__ import annotations

from collections.abc import Iterable

from exlab_wizard.constants import SyncStatus

# Most-attention-worthy first. A folder is summarised by the highest-ranked
# state any child carries: a single failed file dominates a folder of
# otherwise-synced files. ``cleaned`` (data on NAS, local copy gone) ranks
# lowest -- it is the quiet terminal "done" state.
_SEVERITY_ORDER: tuple[str, ...] = (
    SyncStatus.FAILED.value,
    SyncStatus.BLOCKED_BY_VALIDATION.value,
    SyncStatus.PENDING.value,
    SyncStatus.SYNCED.value,
    SyncStatus.CLEANED.value,
)
_SEVERITY_RANK: dict[str, int] = {value: rank for rank, value in enumerate(_SEVERITY_ORDER)}


def sync_rollup(statuses: Iterable[str | None]) -> str | None:
    """Reduce per-file sync statuses to a single worst-of rollup value.

    Returns the highest-severity recognised status among ``statuses``
    (``failed > blocked_by_validation > pending > synced > cleaned``), or
    ``None`` when there is nothing to roll up -- an empty input, or one
    holding only ``None`` / unrecognised values. ``None`` and unknown
    values are ignored rather than raising: a folder of unstatused files
    has no meaningful rollup, and the metadata card renders that as a
    neutral dash via the tolerant icon path (sync_status_icon, §4.5).
    """
    best: str | None = None
    best_rank = len(_SEVERITY_ORDER)  # worse-than-any sentinel
    for status in statuses:
        if status is None:
            continue
        rank = _SEVERITY_RANK.get(status)
        if rank is None:
            continue
        if rank < best_rank:
            best_rank = rank
            best = status
    return best
