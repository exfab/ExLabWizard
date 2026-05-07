"""Orchestrator-mode runtime. Backend Spec §12, §13.

This package implements the orchestrator-only features that activate when
``config.orchestrator.enabled`` is True:

* :class:`StagingWatcher` -- background polling task that walks
  ``staging_root``, writes the initial ``ingest.json`` for each new run,
  and drives the five-state lifecycle described in §13.3.
* :func:`cleanup_eligible` / :func:`clear_run` -- helpers for both the
  manual operator flow and the scheduled background sweeper.
* :func:`list_staged_runs` -- read-side query that backs the Staging UI
  panel and the ``GET /staging`` endpoint.

The orchestrator never touches single-equipment workstations: every
public surface in the ``api/routers/staging.py`` router is gated behind
``config.orchestrator.enabled`` and returns 503 with
``code: "orchestrator_disabled"`` otherwise.
"""

from __future__ import annotations

from exlab_wizard.orchestrator.cleanup import cleanup_eligible, clear_run
from exlab_wizard.orchestrator.staging_query import (
    StagedRunSummary,
    list_staged_runs,
)
from exlab_wizard.orchestrator.staging_watcher import StagingWatcher

__all__ = [
    "StagedRunSummary",
    "StagingWatcher",
    "cleanup_eligible",
    "clear_run",
    "list_staged_runs",
]
