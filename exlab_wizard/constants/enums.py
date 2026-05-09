"""Closed-set enumerations referenced by the cache files, REST API, and UI.

Every enum here uses ``enum.StrEnum`` so that members serialize as their
string values when written through msgspec/JSON. Values are committed by the
design spec -- code MUST NOT add or rename members without a coordinated
schema-version bump.
"""

from __future__ import annotations

from enum import StrEnum


class RunKind(StrEnum):
    """Whether a run is a real experiment or a dry-run/test.

    Stored under ``run_kind`` in creation.json. Backend Spec §4.7, §11.3.
    """

    EXPERIMENTAL = "experimental"
    TEST = "test"


class SyncStatus(StrEnum):
    """State machine for an item in the NAS sync queue.

    Stored under ``sync_status`` in creation.json. Backend Spec §7.1, §11.3.

    ``CLEANED`` is written by the cleanup reaper after the local data files
    are deleted (``retain_cache=True`` keeps ``.exlab-wizard/`` so the run
    remains visible in the local browse view; §7.1.10).
    """

    PENDING = "pending"
    SYNCED = "synced"
    CLEANED = "cleaned"
    FAILED = "failed"
    BLOCKED_BY_VALIDATION = "blocked_by_validation"


class Tier(StrEnum):
    """Validator severity tier. Backend Spec §8.1.6."""

    HARD = "hard"
    SOFT = "soft"


class ProblemClass(StrEnum):
    """Closed set of validator problem classes. Backend Spec §8.1.

    Values are the lower-case form of the member name.
    """

    UNRESOLVED_PLACEHOLDER_TOKEN = "unresolved_placeholder_token"
    LEFTOVER_JINJA_MARKER = "leftover_jinja_marker"
    ILLEGAL_FILESYSTEM_CHARACTER = "illegal_filesystem_character"
    RESERVED_FILESYSTEM_NAME = "reserved_filesystem_name"
    MODE_PREFIX_MISMATCH = "mode_prefix_mismatch"
    ORPHAN = "orphan"
    MISSING_REQUIRED_FIELD = "missing_required_field"
    MALFORMED_YAML_FRONT_MATTER = "malformed_yaml_front_matter"


class FindingKind(StrEnum):
    """What kind of artefact a validator finding refers to.

    Used for grouping findings in the UI. Backend Spec §8.1.
    """

    DIRECTORY_SEGMENT = "directory_segment"
    FILE_NAME = "file_name"
    FILE_CONTENT = "file_content"


class TemplateType(StrEnum):
    """High-level Copier-template category. Backend Spec §5.2."""

    PROJECT = "project"
    EQUIPMENT = "equipment"
    RUN = "run"


class RunScope(StrEnum):
    """Run-scope tag declared in a run template's ``_exlab_run_scope``.

    Backend Spec §5.2.
    """

    EXPERIMENTAL = "experimental"
    TEST = "test"
    BOTH = "both"


class LIMSProjectStatus(StrEnum):
    """Project status as returned by the LIMS REST API.

    The wire format is PascalCase (LIMS convention), so values are kept
    PascalCase rather than the lower-case style of the other enums in this
    module. Backend Spec §7.2.
    """

    PENDING = "Pending"
    ACTIVE = "Active"
    COMPLETED = "Completed"
    ARCHIVED = "Archived"


class LIMSProjectSource(StrEnum):
    """Origin of a LIMS project row presented to the UI. Backend Spec §11.3."""

    LIVE = "live"
    CACHE = "cache"
    OFFLINE_CATALOGUE = "offline_catalogue"


class IngestState(StrEnum):
    """State machine for the NAS-ingest workflow. Backend Spec §13.3."""

    STAGING = "staging"
    COMPLETE = "complete"
    SYNC_QUEUED = "sync_queued"
    SYNC_VERIFIED = "sync_verified"
    CLEARED = "cleared"


class SetupState(StrEnum):
    """First-run / setup-readiness state used by the launcher.

    Backend Spec §4.9.1. Values are the same strings as the member names
    (lower case) by convention.
    """

    INCOMPLETE_NO_CONFIG = "incomplete_no_config"
    INCOMPLETE_MISSING_PATHS = "incomplete_missing_paths"
    INCOMPLETE_NO_EQUIPMENT = "incomplete_no_equipment"
    INCOMPLETE_NO_LIMS = "incomplete_no_lims"
    INCOMPLETE_LIMS_UNREACHABLE = "incomplete_lims_unreachable"
    READY = "ready"


class TransportType(StrEnum):
    """NAS sync transport. Backend Spec §7.1.3."""

    RCLONE = "rclone"
    RSYNC_SSH = "rsync_ssh"


class CompletenessSignal(StrEnum):
    """How a directory signals that its contents are finalized.

    Backend Spec §9 and §13.5.
    """

    SENTINEL_FILE = "sentinel_file"
    MANIFEST = "manifest"


class StagingCleanupMode(StrEnum):
    """How NAS staging directories are eventually purged. Backend Spec §13.7."""

    MANUAL = "manual"
    SCHEDULED = "scheduled"


class PluginStatus(StrEnum):
    """Plugin invocation outcome reported by the host. Backend Spec §6.2.4."""

    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    TIMEOUT = "timeout"
    POLICY_VIOLATION = "policy_violation"


class CreationLevel(StrEnum):
    """Whether a creation.json describes a project root or a run directory.

    Stored under ``level`` in creation.json. Backend Spec §11.3.
    """

    PROJECT = "project"
    RUN = "run"


class OrchestratorTransportType(StrEnum):
    """How the orchestrator delivered run data to the staging area.

    Distinct from :class:`TransportType` (which describes the NAS sync
    transport). Stored under ``transport`` in ingest.json. Backend Spec §13.3.
    """

    SMB_MOUNT = "smb_mount"
    FILE_TRANSFER = "file_transfer"


class FieldType(StrEnum):
    """Allowed input types for README field declarations. Backend Spec §10."""

    STRING = "string"
    TEXT = "text"
    CHOICE = "choice"
    DATE = "date"
    BOOLEAN = "boolean"


class BandwidthDay(StrEnum):
    """Day-of-week values for NAS sync bandwidth windows. Backend Spec §7.1."""

    MON = "mon"
    TUE = "tue"
    WED = "wed"
    THU = "thu"
    FRI = "fri"
    SAT = "sat"
    SUN = "sun"


class SessionKind(StrEnum):
    """Whether a wizard session creates a project root or a run directory.

    Mirrors :class:`CreationLevel` -- the set of values is identical, but the
    two names appear in different contexts (session control flow vs cache
    schema), so they are kept distinct. Backend Spec §4.7.
    """

    PROJECT = "project"
    RUN = "run"


class NextAction(StrEnum):
    """Outcome of a session-store transition. Backend Spec §4.7."""

    NONE = "none"
    AWAITING_INPUT = "awaiting_input"


class AuditScopeKind(StrEnum):
    """Kind discriminator for validator audit scopes. Backend Spec §8.1.

    Values match the ``kind`` field of the AuditScope* TypedDicts; the
    TypedDicts themselves keep ``Literal[...]`` annotations because
    ``TypedDict`` does not accept ``StrEnum`` field types.
    """

    EQUIPMENT_ID = "equipment_id"
    PROJECT_PATH = "project_path"
    ALL = "all"


class DirectoryLevel(StrEnum):
    """Result of classifying a directory in the validator engine.

    Used internally by the validator scope walk; not persisted. Backend
    Spec §8.1.
    """

    EQUIPMENT = "equipment"
    PROJECT = "project"
    RUN = "run"
    TEST_RUN = "test_run"
    TEST_RUNS = "test_runs"
    OTHER = "other"


class Platform(StrEnum):
    """Normalized platform tag used for OS-conditional path dispatch."""

    MACOS = "macos"
    WINDOWS = "windows"
    LINUX = "linux"


class SetupNextAction(StrEnum):
    """Next action returned by ``paths.setup_state_next_action``.

    Backend Spec §4.9.1. ``None`` is also a valid return when the setup
    state is :class:`SetupState.READY`.
    """

    SET_PATHS = "set_paths"
    ADD_EQUIPMENT = "add_equipment"
    CONFIGURE_LIMS = "configure_lims"
    TEST_LIMS = "test_lims"


class SyncHandleState(StrEnum):
    """In-process state of a NAS sync job handle as observed by callers.

    Distinct from :class:`SyncStatus` (which is persisted in creation.json)
    and from the queue's internal ``SyncJobState`` (which tracks the row in
    sync_queue.db). Backend Spec §7.1.
    """

    QUEUED = "queued"
    BLOCKED = "blocked"


class PluginSourceRoot(StrEnum):
    """Origin of a plugin record. Backend Spec §6.1.1."""

    BUNDLED = "bundled"
    LAB = "lab"


class TreeProjectStatus(StrEnum):
    """UI-layer project status displayed in the tree view.

    Distinct from :class:`LIMSProjectStatus` (PascalCase wire values from
    the LIMS API) -- the tree adds a ``DELETED`` sentinel for projects
    removed from LIMS but still present locally.
    """

    ACTIVE = "active"
    ARCHIVED = "archived"
    DELETED = "deleted"
