"""Hard-coded numeric limits, timeouts, and policy ceilings.

Every value here is committed by the design spec and is intentionally NOT
configurable. Code that needs one of these limits must import it from this
module so spec compliance can be audited at a single location.
"""

from __future__ import annotations

from exlab_wizard.constants.filenames import (
    ANSWERS_FILE_NAME,
    CACHE_DIR_NAME,
    README_FILE_NAME,
)

# Maximum wall-clock seconds a plugin may run before the host kills it.
# Backend Spec §6.1.2.
PLUGIN_TIMEOUT_MAX_SECONDS: int = 300

# Maximum resident-set-size cap (in MiB) imposed on every plugin worker.
# Backend Spec §6.1.2.
PLUGIN_MEMORY_MAX_MB: int = 2048

# CPU-seconds budget for the validation phase of a plugin invocation.
# Non-configurable. Backend Spec §6.3.6.
PLUGIN_VALIDATION_CPU_SECONDS: int = 5

# Resident memory cap (MiB) for the validation phase of a plugin invocation.
# Non-configurable. Backend Spec §6.3.6.
PLUGIN_VALIDATION_MEMORY_MB: int = 256

# Wall-clock cap (seconds) for the validation phase of a plugin invocation.
# Non-configurable. Backend Spec §6.3.6.
PLUGIN_VALIDATION_WALL_SECONDS: int = 10

# Maximum number of open file descriptors for a plugin worker (RLIMIT_NOFILE).
# Backend Spec §6.3.3.
PLUGIN_RLIMIT_NOFILE: int = 256

# Maximum size (bytes) of a single IPC frame between host and plugin worker.
# 1 MiB. Backend Spec §6.3.2.
PLUGIN_IPC_FRAME_CAP_BYTES: int = 1024 * 1024

# Plugin host API version that this build of the wizard speaks.
# Backend Spec §6.1.2.
PLUGIN_API_VERSION: str = "1"

# Set of plugin host API versions that this build can negotiate with.
# Backend Spec §6.1.2.
PLUGIN_SUPPORTED_API_VERSIONS: frozenset[str] = frozenset({"1"})

# Path prefixes that plugins are forbidden to write inside a run/project
# directory (the wizard owns these locations). Backend Spec §6.1.5.
PLUGIN_FORBIDDEN_PATH_PREFIXES: tuple[str, ...] = (
    README_FILE_NAME,
    f"{CACHE_DIR_NAME}/",
    ANSWERS_FILE_NAME,
)

# Maximum length, in characters, of a user-entered field label. UI Spec §2.
LABEL_MAX_LENGTH: int = 100

# Maximum length, in characters, of a free-text objective field. UI Spec §2.
OBJECTIVE_MAX_LENGTH: int = 2000

# Number of leading bytes the validator inspects when deciding whether a
# file is binary (and therefore exempt from text-content scanning).
# Backend Spec §8.1.1.
VALIDATOR_BINARY_DETECT_BYTES: int = 8192

# Maximum bytes per line emitted to the central log. Lines longer than this
# are truncated with an ellipsis marker. Backend Spec §4.5.
LOG_LINE_MAX_BYTES: int = 1024

# Server-side session GC interval, in seconds. Backend Spec §4.4.7.
SESSION_GC_AFTER_SECONDS: int = 3600

# Audit-log poll/refresh cadence, in seconds. Backend Spec §4.5.
AUDIT_REFRESH_SECONDS: int = 30

# Maximum seconds the wizard waits for in-flight work to drain when the user
# requests a clean quit. Backend Spec §4.3.2.
QUIT_DRAIN_TIMEOUT_SECONDS: int = 30

# Maximum seconds the wizard waits for in-flight work to drain after a
# SIGTERM from the OS. Backend Spec §4.3.2.
SIGTERM_DRAIN_TIMEOUT_SECONDS: int = 5

# Refresh cadence (seconds) for the tray status submenu. Backend Spec §4.3.2.
TRAY_STATUS_REFRESH_SECONDS: int = 5

# Grace period (seconds) between SIGTERM and SIGKILL for a plugin worker
# that does not exit on time. Backend Spec §6.
WORKER_TIMEOUT_GRACE_SECONDS: int = 1

# Default native-window width, in CSS pixels. Backend Spec §15.
WINDOW_DEFAULT_WIDTH: int = 1280

# Default native-window height, in CSS pixels. Backend Spec §15.
WINDOW_DEFAULT_HEIGHT: int = 800

# Coalesce-window (seconds) for OS notifications so the user is not flooded.
# Backend Spec §15.7.3.
NOTIFICATION_COALESCE_SECONDS: int = 5

# Pre-flight free-disk-space requirement, in MiB, on the run/project target
# volume before the wizard will start a creation. Frontend Spec §4.6.
DISK_SPACE_PREFLIGHT_MIB: int = 100
