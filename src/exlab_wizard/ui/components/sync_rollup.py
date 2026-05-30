"""Folder/run-level sync-status rollup (two-icon presence model, 2026-05-30).

When a run row (left tree) or a folder (metadata pane) is summarised, it carries
a single "worst-of" rollup icon reduced from its children's per-file sync
discriminators. This module owns that reduction as a pure, NiceGUI-free function
so the severity ordering is testable in isolation.

The reduction is over :class:`FileSyncView` (the UI presentation view), not the
schema-committed ``SyncStatus`` enum: severity is a presentation concern, so it
lives here beside its only consumer rather than on the wire enum.
"""

from __future__ import annotations

from collections.abc import Iterable

from exlab_wizard.ui.components.sync_status_icon import FileSyncView, file_sync_view

# Most-attention-worthy first. A folder/run is summarised by the highest-ranked
# view any child carries.
_SEVERITY_ORDER: tuple[FileSyncView, ...] = (
    FileSyncView.MISSING,
    FileSyncView.UPLOAD_FAILED,
    FileSyncView.BLOCKED,
    FileSyncView.LOCAL_ONLY,
    FileSyncView.SYNCED,
    FileSyncView.ON_NAS,
)
_SEVERITY_RANK: dict[FileSyncView, int] = {v: i for i, v in enumerate(_SEVERITY_ORDER)}


def sync_rollup(statuses: Iterable[str | None]) -> str | None:
    """Reduce per-file sync discriminators to a single worst-of view value.

    Returns the highest-severity recognised view's value string
    (``missing > upload_failed > blocked > local_only > synced > on_nas``),
    or ``None`` when there is nothing to roll up (empty, or only ``None`` /
    unrecognised values map to :attr:`FileSyncView.NONE`, which is ignored).
    """
    best: FileSyncView | None = None
    best_rank = len(_SEVERITY_ORDER)
    for status in statuses:
        view = file_sync_view(status)
        rank = _SEVERITY_RANK.get(view)
        if rank is None:  # NONE
            continue
        if rank < best_rank:
            best_rank = rank
            best = view
    return best.value if best is not None else None
