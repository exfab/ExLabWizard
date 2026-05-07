"""Regex patterns and filename-rule literals used by validators.

Each regex is provided as both the raw string (for embedding in error
messages, JSON schemas, or test fixtures) and as a precompiled
``re.Pattern`` object. The validator engine and field validators always use
the compiled form so that compilation cost is paid exactly once at import
time.
"""

from __future__ import annotations

import re

# Equipment-ID grammar: starts with an uppercase letter, followed by uppercase
# letters, digits, and underscores only. Backend Spec §3.1.
EQUIPMENT_ID_REGEX: str = r"^[A-Z][A-Z0-9_]*$"
EQUIPMENT_ID_PATTERN: re.Pattern[str] = re.compile(EQUIPMENT_ID_REGEX)

# Maximum length of an equipment ID, in characters. Backend Spec §3.1.
EQUIPMENT_ID_MAX_LENGTH: int = 32

# Detects unresolved ``<placeholder>`` tokens left over after rendering.
# Validator engine flags every match. Backend Spec §8.1.1.
PLACEHOLDER_ANGLE_BRACKET_REGEX: str = r"<[A-Za-z_][A-Za-z0-9_]*>"
PLACEHOLDER_ANGLE_BRACKET_PATTERN: re.Pattern[str] = re.compile(PLACEHOLDER_ANGLE_BRACKET_REGEX)

# Detects leftover Jinja variable markers (``{{ ... }}``) in rendered output.
# Backend Spec §8.1.1.
PLACEHOLDER_JINJA_VAR_REGEX: str = r"\{\{[^}]*\}\}"
PLACEHOLDER_JINJA_VAR_PATTERN: re.Pattern[str] = re.compile(PLACEHOLDER_JINJA_VAR_REGEX)

# Detects leftover Jinja block markers (``{% ... %}``) in rendered output.
# Backend Spec §8.1.1.
PLACEHOLDER_JINJA_BLOCK_REGEX: str = r"\{%[^%]*%\}"
PLACEHOLDER_JINJA_BLOCK_PATTERN: re.Pattern[str] = re.compile(PLACEHOLDER_JINJA_BLOCK_REGEX)

# Short-form LIMS project identifier used in run directory names.
# Backend Spec §7.2.
PROJECT_SHORT_ID_REGEX: str = r"^PROJ-\d+$"
PROJECT_SHORT_ID_PATTERN: re.Pattern[str] = re.compile(PROJECT_SHORT_ID_REGEX)

# Allowed grammar for template question IDs declared in copier.yml.
# Backend Spec §5.
TEMPLATE_QUESTION_ID_REGEX: str = r"^[a-z][a-z0-9_]*$"
TEMPLATE_QUESTION_ID_PATTERN: re.Pattern[str] = re.compile(TEMPLATE_QUESTION_ID_REGEX)

# Allowed grammar for plugin package names. Backend Spec §6.
PLUGIN_NAME_REGEX: str = r"^[A-Za-z0-9_-]+$"
PLUGIN_NAME_PATTERN: re.Pattern[str] = re.compile(PLUGIN_NAME_REGEX)

# strftime format used when stamping a run directory. ISO 8601 with the
# colons replaced by hyphens so the result is portable across Windows/macOS
# filesystems. Backend Spec §3.
RUN_DATE_STRFTIME: str = "%Y-%m-%dT%H-%M-%S"

# Directory-name prefix for an experimental run. Backend Spec §3.
RUN_DIR_PREFIX: str = "Run_"

# Directory-name prefix for a test run. Backend Spec §3.
TEST_RUN_DIR_PREFIX: str = "TestRun_"

# Sub-directory name (under the equipment root) holding test runs.
# Backend Spec §3.
TEST_RUNS_DIR_NAME: str = "TestRuns"

# Windows-reserved file/directory base names. Comparison MUST be case
# insensitive at the call site (``name.upper() in WINDOWS_RESERVED_NAMES``).
# Backend Spec §8.1.2.
WINDOWS_RESERVED_NAMES: frozenset[str] = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)

# Characters that are illegal in Windows filenames. Backend Spec §8.1.2.
WINDOWS_ILLEGAL_CHARS: str = '<>:"/\\|?*'
