"""msgspec.Struct types for the ExLab-Wizard cache files.

This module is the single source of truth for the on-disk JSON schemas
documented in design spec §11.3 (creation.json), §11.4 (readme_fields.json),
§11.4.1 (equipment.json), §11.4.2 (test_runs.json), and §13.4 (ingest.json).
Every cache reader and writer in the codebase round-trips its bytes through
these Struct types via ``msgspec.json.decode(blob, type=...)`` /
``msgspec.json.encode(obj)``; schema validation happens during decode in
one pass.

Design choices that affect every Struct in this module:

* ``frozen=False`` -- a few fields on ``CreationJson`` (``sync_status``,
  ``validation_overrides``) are mutated in place by ``CreationWriter`` after
  the initial write (Backend Spec §4.4.5; §11.3 "mutated in place" note).
* ``omit_defaults=True`` -- field values that equal their declared default
  are omitted from the encoded JSON. Keeps writes compact and matches the
  on-disk shape shown in spec §11.3 (no nullable-but-null fields, no empty
  arrays for unused sub-blocks).
* ``forbid_unknown_fields=False`` -- unknown fields are silently preserved
  on round-trip via the writer's ``raw_extras`` mechanism in
  ``cache/creation_writer.py``. This is required by §11.9.3 writer policy
  rule 2: a v0.7 writer mutating a file written by a v0.8 writer MUST NOT
  drop the v0.8 fields it doesn't recognize.

The ``validation_overrides`` field is typed as ``list[dict[str, Any]]``
rather than ``list[OverrideEntry | TombstoneEntry]`` because msgspec
struct unions require an explicit tag (``tag`` or ``tag_field``) and the
discriminator the spec mandates -- the boolean ``revoked`` flag -- cannot
be used as a tag (msgspec tags must be strings or ints). Helpers
``override_entry_to_dict``, ``tombstone_entry_to_dict``, and
``parse_validation_override_entry`` bridge the wire form (dict) and the
typed form (Struct) for callers that want either representation.
"""

from __future__ import annotations

from typing import Any

import msgspec
from msgspec import Struct
from msgspec import json as msgspec_json
from msgspec import structs as msgspec_structs

from exlab_wizard.constants import (
    CompletenessSignal,
    CreationLevel,
    IngestState,
    LIMSProjectSource,
    OrchestratorTransportType,
    PluginStatus,
    RunKind,
    RunScope,
    SyncStatus,
)

__all__ = [
    "CreationJson",
    "EquipmentJson",
    "IngestJson",
    "LimsProjectBlock",
    "OrchestratorBlock",
    "OverrideEntry",
    "PathsBlock",
    "PluginApplied",
    "PluginIsolation",
    "ReadmeFieldsJson",
    "TemplateBlock",
    "TestRunsJson",
    "TombstoneEntry",
    "msgspec_json",
    "override_entry_to_dict",
    "parse_validation_override_entry",
    "tombstone_entry_to_dict",
]


# ---------------------------------------------------------------------------
# creation.json sub-blocks (§11.3)
# ---------------------------------------------------------------------------


class LimsProjectBlock(
    Struct,
    omit_defaults=True,
    forbid_unknown_fields=False,
):
    """LIMS-side project identity captured at creation time. Spec §11.3.

    Required on project- and run-level ``creation.json`` files at schema
    version >= 1.5. ``source`` and ``cache_freshness_at_use`` were added
    in 1.8; on a 1.7 file they are absent and read as ``"live"`` /
    ``None`` per the migration policy in §11.9.2.
    """

    uid: str
    short_id: str
    name_at_creation: str
    source: LIMSProjectSource = LIMSProjectSource.LIVE
    cache_freshness_at_use: str | None = None


class TemplateBlock(
    Struct,
    omit_defaults=True,
    forbid_unknown_fields=False,
):
    """Template provenance captured at creation time. Spec §11.3."""

    name: str
    version: str
    source_path: str
    # ``None`` when the source template did not declare ``_exlab_run_scope``
    # (legal for project / equipment templates per Spec §5.2). Persisted as
    # an omitted field thanks to ``omit_defaults=True``.
    run_scope: RunScope | None = None


class PluginIsolation(
    Struct,
    omit_defaults=True,
    forbid_unknown_fields=False,
):
    """Plugin worker isolation telemetry. Spec §6.2.4 / §11.3 (added 1.3)."""

    duration_ms: int
    exit_code: int
    peak_memory_mb: int


class PluginApplied(
    Struct,
    omit_defaults=True,
    forbid_unknown_fields=False,
):
    """Per-plugin invocation record written into ``plugins_applied``.

    Spec §6.2.4 / §11.3. ``isolation`` was added in schema version 1.3;
    older readers ignore it and older writers treat its absence as a no-op.
    """

    plugin: str
    version: str
    files_affected: list[str]
    status: PluginStatus
    isolation: PluginIsolation | None = None


class PathsBlock(
    Struct,
    omit_defaults=True,
    forbid_unknown_fields=False,
):
    """Resolved on-disk paths captured at creation time. Spec §11.3."""

    local: str
    nas: str


class OrchestratorBlock(
    Struct,
    omit_defaults=True,
    forbid_unknown_fields=False,
):
    """Orchestrator-mode metadata. Spec §11.3 / §13 / Redesign Spec §3.3.

    Always present on a v1 ``creation.json``; the redesign collapses the
    single-equipment / orchestrator distinction into a per-equipment sync
    role and the orchestrator block always carries the producing device's
    identity.

    The ``equipment_label`` / ``completeness_signal`` /
    ``sentinel_filename`` / ``manifest_filename`` fields travel with the
    push so the receiving orchestrator can auto-discover received
    equipment (Redesign Spec §3.3) without a per-equipment registry of
    its own. They are optional for forward-compat with creation.json
    files written by earlier writers.
    """

    enabled: bool
    host: str
    label: str
    equipment_label: str | None = None
    completeness_signal: CompletenessSignal | None = None
    sentinel_filename: str | None = None
    manifest_filename: str | None = None


# ---------------------------------------------------------------------------
# Validation overrides (§11.3 -- two entry shapes, append-only list)
# ---------------------------------------------------------------------------


class OverrideEntry(
    Struct,
    omit_defaults=True,
    forbid_unknown_fields=False,
):
    """Operator-recorded validation override entry. Spec §11.3.

    ``revoked`` is ``False`` on every override entry. The default is kept
    for ergonomic construction; the writer helper
    ``override_entry_to_dict`` ensures the field is always present on the
    wire (the spec requires it on every entry).
    """

    id: str
    problem_class: str
    operator: str
    recorded_at: str
    reason: str
    revoked: bool = False
    expires_at: str | None = None


class TombstoneEntry(
    Struct,
    omit_defaults=True,
    forbid_unknown_fields=False,
):
    """Tombstone entry that revokes a prior override by ``id``. Spec §11.3.

    ``revoked`` is ``True`` on every tombstone. The default is kept for
    ergonomic construction; the writer helper ``tombstone_entry_to_dict``
    ensures the field is always present on the wire.
    """

    id: str
    revokes: str
    operator: str
    recorded_at: str
    reason: str
    revoked: bool = True


def override_entry_to_dict(entry: OverrideEntry) -> dict[str, Any]:
    """Serialize an ``OverrideEntry`` to a wire-form dict.

    Uses ``msgspec.structs.asdict`` so that ``revoked`` (which equals its
    default of ``False`` on every override) is included. Spec §11.3
    requires the field on every entry.
    """
    return msgspec_structs.asdict(entry)


def tombstone_entry_to_dict(entry: TombstoneEntry) -> dict[str, Any]:
    """Serialize a ``TombstoneEntry`` to a wire-form dict. Mirrors
    ``override_entry_to_dict``; ``revoked`` is always emitted.
    """
    return msgspec_structs.asdict(entry)


def parse_validation_override_entry(
    entry: dict[str, Any],
) -> OverrideEntry | TombstoneEntry:
    """Inspect ``entry["revoked"]`` and ``entry["revokes"]`` to decide
    which Struct shape to convert into.

    The spec says tombstones carry ``revoked: True`` AND a ``revokes``
    pointer; overrides carry ``revoked: False`` AND a ``problem_class``.
    Use ``revoked`` first, ``revokes`` as a tiebreaker for old files
    (pre-1.6) where ``revokes`` may have been the only discriminator.
    """
    is_tombstone = bool(entry.get("revoked", False)) or "revokes" in entry
    if is_tombstone:
        return msgspec.convert(entry, type=TombstoneEntry)
    return msgspec.convert(entry, type=OverrideEntry)


# ---------------------------------------------------------------------------
# Top-level creation.json (§11.3)
# ---------------------------------------------------------------------------


class CreationJson(
    Struct,
    omit_defaults=True,
    forbid_unknown_fields=False,
):
    """Top-level ``creation.json`` payload at schema version 1.8.

    Reading: ``msgspec.json.decode(blob, type=CreationJson)`` validates
    every required field and rejects type errors in one pass. Unknown
    fields are silently ignored at the Struct boundary; the writer
    re-serializes them via ``cache/creation_writer.py``'s extras pass
    so forward-compat is preserved.

    Writing: every write goes through ``CacheWriter``; direct
    ``msgspec.json.encode(payload)`` calls are reserved for tests and
    for the writer's tempfile pass.
    """

    schema_version: str
    created_at: str
    created_by: str
    level: CreationLevel
    run_kind: RunKind
    lims_project: LimsProjectBlock
    template: TemplateBlock
    variables: dict[str, Any]
    paths: PathsBlock
    plugins_applied: list[PluginApplied] = []
    orchestrator: OrchestratorBlock | None = None
    sync_status: SyncStatus = SyncStatus.PENDING
    validation_overrides: list[dict[str, Any]] = []


# ---------------------------------------------------------------------------
# readme_fields.json (§11.4)
# ---------------------------------------------------------------------------


class ReadmeFieldsJson(
    Struct,
    omit_defaults=True,
    forbid_unknown_fields=False,
):
    """``readme_fields.json`` at schema version 1.1. Spec §11.4."""

    schema_version: str
    generated_at: str
    core_fields: dict[str, str]
    system_fields: dict[str, Any]
    template_fields: dict[str, Any] = {}
    config_fields: dict[str, Any] = {}
    custom_fields: list[dict[str, str]] = []


# ---------------------------------------------------------------------------
# equipment.json (§11.4.1)
# ---------------------------------------------------------------------------


class EquipmentJson(
    Struct,
    omit_defaults=True,
    forbid_unknown_fields=False,
):
    """``equipment.json`` at schema version 1.0. Spec §11.4.1."""

    schema_version: str
    id: str
    label: str
    configured_local_root: str
    configured_nas_root: str
    first_seen_at: str
    last_modified_at: str


# ---------------------------------------------------------------------------
# test_runs.json (§11.4.2)
# ---------------------------------------------------------------------------


class TestRunsJson(
    Struct,
    omit_defaults=True,
    forbid_unknown_fields=False,
):
    """``test_runs.json`` marker at schema version 1.0. Spec §11.4.2.

    Filename retained from v0.5 for backward compatibility even though the
    parent folder was renamed to ``TestRuns/`` in v0.6.
    """

    schema_version: str
    created_at: str
    project: str
    equipment: str
    run_kind: RunKind = RunKind.TEST


# ---------------------------------------------------------------------------
# ingest.json (§13.4) -- orchestrator-only staging lifecycle record
# ---------------------------------------------------------------------------


class IngestJson(
    Struct,
    omit_defaults=True,
    forbid_unknown_fields=False,
):
    """``ingest.json`` orchestrator staging record at schema version 1.1. Spec §13.4.

    Written by the orchestrator only (not by equipment workstations). The
    ``history`` list is append-only per §13: lifecycle transitions are
    recorded, never overwritten. ``current_state`` mirrors the most recent
    history entry's ``state`` for fast read-without-walk access.

    History entries are loose dicts because the optional fields per state
    (``files_received`` / ``bytes_received`` on ``complete``; ``nas_path`` /
    ``checksum_file`` on ``sync_verified``) make a strict type a nuisance.
    The state-machine validation is performed by the writer.
    """

    schema_version: str
    project_name: str
    equipment_id: str
    run_kind: RunKind
    run_path: str
    transport: OrchestratorTransportType
    current_state: IngestState
    history: list[dict[str, Any]] = []
