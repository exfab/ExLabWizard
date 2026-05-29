"""Orchestrator-mode runtime. Backend Spec §12, §13.

This package implements the orchestrator-mode features that activate when a
``config.orchestrator.staging_root`` is configured or any ``nas``-mode
equipment exists:

* :class:`QuiescenceSyncPoller` -- background polling task that discovers
  every run pending NAS sync (orchestrator-staged *and* ``nas``-mode) and
  enqueues a run once it has at least one quiescent file. It is the single
  operator-free auto-sync trigger (operator-free per-file NAS sync design,
  2026-05-21), superseding the retired sentinel/manifest ``StagingWatcher``.
* :func:`list_staged_runs` -- read-side query that backs the Staging UI
  panel and the ``GET /staging`` endpoint.
"""

from __future__ import annotations

from exlab_wizard.orchestrator.quiescence_poller import QuiescenceSyncPoller
from exlab_wizard.orchestrator.staging_query import (
    StagedRunSummary,
    list_staged_runs,
)

__all__ = [
    "QuiescenceSyncPoller",
    "StagedRunSummary",
    "list_staged_runs",
]
