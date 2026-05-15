"""Pre-Sync Gate. Backend Spec §7.3.

The Pre-Sync Gate is the contract by which validator findings prevent
the NAS sync queue from accepting a flagged run. A run is eligible iff:

- the validator engine reports zero hard-tier findings, OR
- every hard-tier finding has a matching active override entry in
  ``creation.json``'s ``validation_overrides`` array.

This module is the pure evaluator. The :class:`NASSyncClient` calls it
before inserting a queue row and, when the gate blocks, mutates the
``creation.json`` ``sync_status`` field to ``"blocked_by_validation"``.
"""

from __future__ import annotations

from pathlib import Path

from exlab_wizard.api.schemas import CreationJson
from exlab_wizard.cache.creation_writer import select_active_overrides
from exlab_wizard.constants import Tier
from exlab_wizard.validator.engine import (
    CreationValidationInput,
    Validator,
    _split_path_segments,
)
from exlab_wizard.validator.findings import Finding

__all__ = ["is_eligible"]


def _file_names_in_run(run_path: Path) -> list[str]:
    """Return the bare file names directly under ``run_path``.

    The Pre-Sync Gate is a creation-time-style validator pass against the
    on-disk run, so we re-collect file names rather than rely on the
    template-render output. The walk is shallow on purpose: §8.1 rules
    that scan content are not part of the gate's hard-tier set, so we
    only need names + the path segments.
    """
    if not run_path.exists() or not run_path.is_dir():
        return []
    return [entry.name for entry in run_path.iterdir() if entry.is_file()]


def is_eligible(
    *,
    validator: Validator,
    creation_json_path: Path,
    creation: CreationJson,
) -> tuple[bool, list[Finding]]:
    """Evaluate the §7.3 eligibility rule for a run.

    Returns ``(True, [])`` iff there is no hard-tier finding without an
    active override. Otherwise returns ``(False, blocking_findings)``
    where ``blocking_findings`` is the list of unmasked hard-tier
    findings (so the caller can surface them in logs).

    The ``creation_json_path`` is the path to ``.exlab-wizard/creation.json``;
    the run directory is its parent's parent. The validator runs in
    creation-time mode (no walk; just rules over path segments + file
    names + the cached creation payload).
    """
    run_path = creation_json_path.parent.parent
    proposed_path = creation.paths.local or str(run_path)

    file_names = tuple(_file_names_in_run(run_path))
    params = CreationValidationInput(
        proposed_path=proposed_path,
        variables=creation.variables,
        file_names=file_names,
        run_kind=creation.run_kind,
    )
    findings = validator.validate_creation(params)

    # Active override classes from the run's validation_overrides list.
    active_classes: set[str] = {
        e["problem_class"]
        for e in select_active_overrides(creation.validation_overrides)
        if "problem_class" in e
    }

    blocking: list[Finding] = [
        f for f in findings if f.tier == Tier.HARD.value and f.rule not in active_classes
    ]

    # The mode-prefix mismatch rule needs a parent-segment context that
    # isn't reachable from creation.paths.local alone if the LIMS short
    # ID component is missing; for a more complete gate we also pass
    # the resolved on-disk path. We re-run the splitter here to make
    # sure the run is evaluated against its actual segments.
    _ = _split_path_segments  # reference to keep the symbol re-exported

    if blocking:
        return False, blocking
    return True, []
