"""Verify the ``StrEnum`` value strings match the design spec verbatim.

These enums travel across the JSON cache files and the REST API, so any
silent rename or value change is a wire-format break. Each test pins one
member's serialized value.
"""

from __future__ import annotations

from enum import StrEnum

from exlab_wizard.constants import enums


def test_run_kind_values() -> None:
    # Backend Spec §4.7, §11.3.
    assert issubclass(enums.RunKind, StrEnum)
    assert enums.RunKind.EXPERIMENTAL.value == "experimental"
    assert enums.RunKind.TEST.value == "test"
    assert {m.value for m in enums.RunKind} == {"experimental", "test"}


def test_run_kind_is_str() -> None:
    # StrEnum members must compare equal to their string value so msgspec /
    # JSON serialize them transparently.
    assert enums.RunKind.EXPERIMENTAL == "experimental"
    assert enums.RunKind.TEST == "test"


def test_sync_status_values() -> None:
    # Backend Spec §7.1, §11.3.
    assert issubclass(enums.SyncStatus, StrEnum)
    assert enums.SyncStatus.PENDING.value == "pending"
    assert enums.SyncStatus.SYNCED.value == "synced"
    assert enums.SyncStatus.CLEANED.value == "cleaned"
    assert enums.SyncStatus.FAILED.value == "failed"
    assert enums.SyncStatus.BLOCKED_BY_VALIDATION.value == "blocked_by_validation"
    assert {m.value for m in enums.SyncStatus} == {
        "pending",
        "synced",
        "cleaned",
        "failed",
        "blocked_by_validation",
    }


def test_tier_values() -> None:
    # Backend Spec §8.1.6.
    assert issubclass(enums.Tier, StrEnum)
    assert enums.Tier.HARD.value == "hard"
    assert enums.Tier.SOFT.value == "soft"
    assert {m.value for m in enums.Tier} == {"hard", "soft"}


def test_problem_class_values() -> None:
    # Backend Spec §8.1. Values are the lower-case form of the member name.
    assert issubclass(enums.ProblemClass, StrEnum)
    assert enums.ProblemClass.UNRESOLVED_PLACEHOLDER_TOKEN.value == "unresolved_placeholder_token"
    assert enums.ProblemClass.LEFTOVER_JINJA_MARKER.value == "leftover_jinja_marker"
    assert enums.ProblemClass.ILLEGAL_FILESYSTEM_CHARACTER.value == "illegal_filesystem_character"
    assert enums.ProblemClass.RESERVED_FILESYSTEM_NAME.value == "reserved_filesystem_name"
    assert enums.ProblemClass.MODE_PREFIX_MISMATCH.value == "mode_prefix_mismatch"
    assert enums.ProblemClass.ORPHAN.value == "orphan"
    assert enums.ProblemClass.MISSING_REQUIRED_FIELD.value == "missing_required_field"
    assert enums.ProblemClass.MALFORMED_YAML_FRONT_MATTER.value == "malformed_yaml_front_matter"
    assert {m.value for m in enums.ProblemClass} == {
        "unresolved_placeholder_token",
        "leftover_jinja_marker",
        "illegal_filesystem_character",
        "reserved_filesystem_name",
        "mode_prefix_mismatch",
        "orphan",
        "missing_required_field",
        "malformed_yaml_front_matter",
    }


def test_finding_kind_values() -> None:
    # Backend Spec §8.1.
    assert issubclass(enums.FindingKind, StrEnum)
    assert enums.FindingKind.DIRECTORY_SEGMENT.value == "directory_segment"
    assert enums.FindingKind.FILE_NAME.value == "file_name"
    assert enums.FindingKind.FILE_CONTENT.value == "file_content"
    assert {m.value for m in enums.FindingKind} == {
        "directory_segment",
        "file_name",
        "file_content",
    }


def test_template_type_values() -> None:
    # Backend Spec §5.2.
    assert issubclass(enums.TemplateType, StrEnum)
    assert enums.TemplateType.PROJECT.value == "project"
    assert enums.TemplateType.EQUIPMENT.value == "equipment"
    assert enums.TemplateType.RUN.value == "run"
    assert {m.value for m in enums.TemplateType} == {"project", "equipment", "run"}


def test_run_scope_values() -> None:
    # Backend Spec §5.2 -- ``_exlab_run_scope``.
    assert issubclass(enums.RunScope, StrEnum)
    assert enums.RunScope.EXPERIMENTAL.value == "experimental"
    assert enums.RunScope.TEST.value == "test"
    assert enums.RunScope.BOTH.value == "both"
    assert {m.value for m in enums.RunScope} == {"experimental", "test", "both"}


def test_lims_project_status_values_pascal_case() -> None:
    # Backend Spec §7.2 -- LIMS API uses PascalCase wire values.
    assert issubclass(enums.LIMSProjectStatus, StrEnum)
    assert enums.LIMSProjectStatus.PENDING.value == "Pending"
    assert enums.LIMSProjectStatus.ACTIVE.value == "Active"
    assert enums.LIMSProjectStatus.COMPLETED.value == "Completed"
    assert enums.LIMSProjectStatus.ARCHIVED.value == "Archived"
    assert {m.value for m in enums.LIMSProjectStatus} == {
        "Pending",
        "Active",
        "Completed",
        "Archived",
    }


def test_lims_project_source_values() -> None:
    # Backend Spec §11.3.
    assert issubclass(enums.LIMSProjectSource, StrEnum)
    assert enums.LIMSProjectSource.LIVE.value == "live"
    assert enums.LIMSProjectSource.CACHE.value == "cache"
    assert enums.LIMSProjectSource.OFFLINE_CATALOGUE.value == "offline_catalogue"
    assert {m.value for m in enums.LIMSProjectSource} == {
        "live",
        "cache",
        "offline_catalogue",
    }


def test_ingest_state_values() -> None:
    # Backend Spec §13.3.
    assert issubclass(enums.IngestState, StrEnum)
    assert enums.IngestState.STAGING.value == "staging"
    assert enums.IngestState.COMPLETE.value == "complete"
    assert enums.IngestState.SYNC_QUEUED.value == "sync_queued"
    assert enums.IngestState.SYNC_VERIFIED.value == "sync_verified"
    assert enums.IngestState.CLEARED.value == "cleared"
    assert {m.value for m in enums.IngestState} == {
        "staging",
        "complete",
        "sync_queued",
        "sync_verified",
        "cleared",
    }


def test_setup_state_values() -> None:
    # Backend Spec §4.9.1.
    assert issubclass(enums.SetupState, StrEnum)
    assert enums.SetupState.INCOMPLETE_NO_CONFIG.value == "incomplete_no_config"
    assert enums.SetupState.INCOMPLETE_MISSING_PATHS.value == "incomplete_missing_paths"
    assert enums.SetupState.INCOMPLETE_NO_EQUIPMENT.value == "incomplete_no_equipment"
    assert enums.SetupState.INCOMPLETE_NO_LIMS.value == "incomplete_no_lims"
    assert enums.SetupState.INCOMPLETE_LIMS_UNREACHABLE.value == "incomplete_lims_unreachable"
    assert enums.SetupState.READY.value == "ready"
    assert {m.value for m in enums.SetupState} == {
        "incomplete_no_config",
        "incomplete_missing_paths",
        "incomplete_no_equipment",
        "incomplete_no_lims",
        "incomplete_lims_unreachable",
        "ready",
    }


def test_transport_type_values() -> None:
    # Backend Spec §7.1.3.
    assert issubclass(enums.TransportType, StrEnum)
    assert enums.TransportType.RCLONE.value == "rclone"
    assert enums.TransportType.RSYNC_SSH.value == "rsync_ssh"
    assert {m.value for m in enums.TransportType} == {"rclone", "rsync_ssh"}


def test_completeness_signal_values() -> None:
    # Backend Spec §9, §13.5.
    assert issubclass(enums.CompletenessSignal, StrEnum)
    assert enums.CompletenessSignal.SENTINEL_FILE.value == "sentinel_file"
    assert enums.CompletenessSignal.MANIFEST.value == "manifest"
    assert {m.value for m in enums.CompletenessSignal} == {"sentinel_file", "manifest"}


def test_staging_cleanup_mode_values() -> None:
    # Backend Spec §13.7.
    assert issubclass(enums.StagingCleanupMode, StrEnum)
    assert enums.StagingCleanupMode.MANUAL.value == "manual"
    assert enums.StagingCleanupMode.SCHEDULED.value == "scheduled"
    assert {m.value for m in enums.StagingCleanupMode} == {"manual", "scheduled"}


def test_plugin_status_values() -> None:
    # Backend Spec §6.2.4.
    assert issubclass(enums.PluginStatus, StrEnum)
    assert enums.PluginStatus.SUCCESS.value == "success"
    assert enums.PluginStatus.FAILED.value == "failed"
    assert enums.PluginStatus.SKIPPED.value == "skipped"
    assert enums.PluginStatus.TIMEOUT.value == "timeout"
    assert enums.PluginStatus.POLICY_VIOLATION.value == "policy_violation"
    assert {m.value for m in enums.PluginStatus} == {
        "success",
        "failed",
        "skipped",
        "timeout",
        "policy_violation",
    }


def test_enums_re_exported_from_package() -> None:
    # All enum classes must be re-exported from ``exlab_wizard.constants``.
    from exlab_wizard import constants

    assert constants.RunKind is enums.RunKind
    assert constants.SyncStatus is enums.SyncStatus
    assert constants.Tier is enums.Tier
    assert constants.ProblemClass is enums.ProblemClass
    assert constants.FindingKind is enums.FindingKind
    assert constants.TemplateType is enums.TemplateType
    assert constants.RunScope is enums.RunScope
    assert constants.LIMSProjectStatus is enums.LIMSProjectStatus
    assert constants.LIMSProjectSource is enums.LIMSProjectSource
    assert constants.IngestState is enums.IngestState
    assert constants.SetupState is enums.SetupState
    assert constants.TransportType is enums.TransportType
    assert constants.CompletenessSignal is enums.CompletenessSignal
    assert constants.StagingCleanupMode is enums.StagingCleanupMode
    assert constants.PluginStatus is enums.PluginStatus
