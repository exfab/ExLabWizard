"""Verify the numeric limits and policy ceilings.

Each value here is hard-coded per the design spec and must NOT be made
configurable. These tests guard against accidental tweaks.
"""

from __future__ import annotations

from exlab_wizard.constants import limits


def test_plugin_timeout_max_seconds() -> None:
    # Backend Spec §6.1.2.
    assert limits.PLUGIN_TIMEOUT_MAX_SECONDS == 300


def test_plugin_memory_max_mb() -> None:
    # Backend Spec §6.1.2.
    assert limits.PLUGIN_MEMORY_MAX_MB == 2048


def test_plugin_validation_cpu_seconds() -> None:
    # Non-configurable. Backend Spec §6.3.6.
    assert limits.PLUGIN_VALIDATION_CPU_SECONDS == 5


def test_plugin_validation_memory_mb() -> None:
    # Non-configurable. Backend Spec §6.3.6.
    assert limits.PLUGIN_VALIDATION_MEMORY_MB == 256


def test_plugin_validation_wall_seconds() -> None:
    # Non-configurable. Backend Spec §6.3.6.
    assert limits.PLUGIN_VALIDATION_WALL_SECONDS == 10


def test_plugin_rlimit_nofile() -> None:
    # Backend Spec §6.3.3.
    assert limits.PLUGIN_RLIMIT_NOFILE == 256


def test_plugin_ipc_frame_cap_bytes_is_one_mib() -> None:
    # Backend Spec §6.3.2 -- 1 MiB.
    assert limits.PLUGIN_IPC_FRAME_CAP_BYTES == 1024 * 1024
    assert limits.PLUGIN_IPC_FRAME_CAP_BYTES == 1_048_576


def test_plugin_api_version() -> None:
    # Backend Spec §6.1.2.
    assert limits.PLUGIN_API_VERSION == "1"
    assert isinstance(limits.PLUGIN_API_VERSION, str)


def test_plugin_supported_api_versions() -> None:
    # Backend Spec §6.1.2.
    assert limits.PLUGIN_SUPPORTED_API_VERSIONS == frozenset({"1"})
    assert isinstance(limits.PLUGIN_SUPPORTED_API_VERSIONS, frozenset)
    # The current API version must always be in the supported set.
    assert limits.PLUGIN_API_VERSION in limits.PLUGIN_SUPPORTED_API_VERSIONS


def test_plugin_forbidden_path_prefixes() -> None:
    # Backend Spec §6.1.5.
    assert limits.PLUGIN_FORBIDDEN_PATH_PREFIXES == (
        "README.md",
        ".exlab-wizard/",
        ".exlab-answers.yml",
    )
    assert isinstance(limits.PLUGIN_FORBIDDEN_PATH_PREFIXES, tuple)


def test_label_max_length() -> None:
    # UI Spec §2.
    assert limits.LABEL_MAX_LENGTH == 100


def test_objective_max_length() -> None:
    # UI Spec §2.
    assert limits.OBJECTIVE_MAX_LENGTH == 2000


def test_validator_binary_detect_bytes() -> None:
    # Backend Spec §8.1.1.
    assert limits.VALIDATOR_BINARY_DETECT_BYTES == 8192


def test_log_line_max_bytes() -> None:
    # Backend Spec §4.5.
    assert limits.LOG_LINE_MAX_BYTES == 1024


def test_session_gc_after_seconds() -> None:
    # Backend Spec §4.4.7.
    assert limits.SESSION_GC_AFTER_SECONDS == 3600


def test_audit_refresh_seconds() -> None:
    # Backend Spec §4.5.
    assert limits.AUDIT_REFRESH_SECONDS == 30


def test_quit_drain_timeout_seconds() -> None:
    # Backend Spec §4.3.2.
    assert limits.QUIT_DRAIN_TIMEOUT_SECONDS == 30


def test_sigterm_drain_timeout_seconds() -> None:
    # Backend Spec §4.3.2.
    assert limits.SIGTERM_DRAIN_TIMEOUT_SECONDS == 5


def test_tray_status_refresh_seconds() -> None:
    # Backend Spec §4.3.2.
    assert limits.TRAY_STATUS_REFRESH_SECONDS == 5


def test_worker_timeout_grace_seconds() -> None:
    # Backend Spec §6 (SIGTERM then SIGKILL after 1 second).
    assert limits.WORKER_TIMEOUT_GRACE_SECONDS == 1


def test_window_default_size() -> None:
    # Backend Spec §15.
    assert limits.WINDOW_DEFAULT_WIDTH == 1280
    assert limits.WINDOW_DEFAULT_HEIGHT == 800


def test_notification_coalesce_seconds() -> None:
    # Backend Spec §15.7.3.
    assert limits.NOTIFICATION_COALESCE_SECONDS == 5


def test_disk_space_preflight_mib() -> None:
    # Frontend Spec §4.6 pre-flight.
    assert limits.DISK_SPACE_PREFLIGHT_MIB == 100


def test_limits_re_exported_from_package() -> None:
    # The most-used numeric constants must round-trip through the top-level
    # package so callers can ``from exlab_wizard.constants import ...``.
    from exlab_wizard import constants

    assert constants.PLUGIN_TIMEOUT_MAX_SECONDS == 300
    assert constants.PLUGIN_MEMORY_MAX_MB == 2048
    assert constants.PLUGIN_VALIDATION_CPU_SECONDS == 5
    assert constants.PLUGIN_VALIDATION_MEMORY_MB == 256
    assert constants.PLUGIN_VALIDATION_WALL_SECONDS == 10
    assert constants.PLUGIN_RLIMIT_NOFILE == 256
    assert constants.PLUGIN_IPC_FRAME_CAP_BYTES == 1024 * 1024
    assert constants.PLUGIN_API_VERSION == "1"
    assert constants.PLUGIN_SUPPORTED_API_VERSIONS == frozenset({"1"})
    assert constants.PLUGIN_FORBIDDEN_PATH_PREFIXES == (
        "README.md",
        ".exlab-wizard/",
        ".exlab-answers.yml",
    )
    assert constants.LABEL_MAX_LENGTH == 100
    assert constants.OBJECTIVE_MAX_LENGTH == 2000
    assert constants.VALIDATOR_BINARY_DETECT_BYTES == 8192
    assert constants.LOG_LINE_MAX_BYTES == 1024
    assert constants.SESSION_GC_AFTER_SECONDS == 3600
    assert constants.AUDIT_REFRESH_SECONDS == 30
    assert constants.QUIT_DRAIN_TIMEOUT_SECONDS == 30
    assert constants.SIGTERM_DRAIN_TIMEOUT_SECONDS == 5
    assert constants.TRAY_STATUS_REFRESH_SECONDS == 5
    assert constants.WORKER_TIMEOUT_GRACE_SECONDS == 1
    assert constants.WINDOW_DEFAULT_WIDTH == 1280
    assert constants.WINDOW_DEFAULT_HEIGHT == 800
    assert constants.NOTIFICATION_COALESCE_SECONDS == 5
    assert constants.DISK_SPACE_PREFLIGHT_MIB == 100
