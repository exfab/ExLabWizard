"""Path composition + OS-appropriate directories + equipment-id canonicalization.

Backend Spec §3.1 (equipment-ID format), §4.9 (setup states), §9 (config
locations), §15 (state directory locations), and §16.3 (central log path).

This module is a leaf in the import graph (only depends on stdlib +
constants + errors). It is loaded early by the launcher, so every helper
is synchronous and side-effect-free unless explicitly named otherwise
(e.g. ``ensure_state_dir`` mkdirs).
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from exlab_wizard.constants import (
    APP_NAME,
    CACHE_DIR_NAME,
    CENTRAL_LOG_FILE,
    CREATION_JSON_NAME,
    EQUIPMENT_ID_MAX_LENGTH,
    EQUIPMENT_ID_PATTERN,
    EQUIPMENT_JSON_NAME,
    INGEST_JSON_NAME,
    PROJECT_NAME_MAX_LENGTH,
    PROJECT_SHORT_ID_PATTERN,
    README_FIELDS_JSON_NAME,
    RUN_DATE_STRFTIME,
    RUN_DIR_PREFIX,
    TEST_RUN_DIR_PREFIX,
    TEST_RUNS_DIR_NAME,
    WINDOWS_ILLEGAL_CHARS,
    WINDOWS_RESERVED_NAMES,
    Platform,
    RunKind,
    SetupNextAction,
    SetupState,
)
from exlab_wizard.errors import ConfigError

if TYPE_CHECKING:
    from exlab_wizard.config.models import Config

__all__ = [
    "cache_dir",
    "canonicalize_equipment_id",
    "compose_project_path",
    "compose_run_path",
    "creation_json_path",
    "default_orchestrator_staging_root",
    "ensure_central_log_dir",
    "ensure_dir",
    "ensure_state_dir",
    "equipment_json_path",
    "evaluate_setup_state",
    "ingest_json_path",
    "is_run_dir",
    "is_test_run_dir",
    "os_cache_path",
    "os_central_log_path",
    "os_config_path",
    "os_state_path",
    "readme_fields_json_path",
    "run_dir_stem",
    "setup_state_missing",
    "setup_state_next_action",
    "validate_project_short_id",
]


# ---------------------------------------------------------------------------
# OS-aware path helpers (no side effects)
# ---------------------------------------------------------------------------


def _platform() -> Platform:
    """Return a normalized platform tag for OS-conditional path dispatch."""
    match sys.platform:
        case "darwin":
            return Platform.MACOS
        case "win32":
            return Platform.WINDOWS
        case _:
            return Platform.LINUX


def _home() -> Path:
    """Return the operator's home directory.

    Centralized so test fixtures can monkeypatch ``Path.home`` once and have
    every helper see the override.
    """
    return Path.home()


def _env_path(var: str, fallback: Path) -> Path:
    """Return ``Path(os.environ[var])`` if the var is set and non-empty, else ``fallback``."""
    value = os.environ.get(var)
    return Path(value) if value else fallback


def os_config_path() -> Path:
    """Return the OS-appropriate path of ``config.yaml``. Backend Spec §9."""
    match _platform():
        case Platform.MACOS:
            return _home() / "Library" / "Application Support" / APP_NAME / "config.yaml"
        case Platform.WINDOWS:
            return _env_path("APPDATA", _home() / "AppData" / "Roaming") / APP_NAME / "config.yaml"
        case Platform.LINUX:
            return _env_path("XDG_CONFIG_HOME", _home() / ".config") / APP_NAME / "config.yaml"


def os_state_path() -> Path:
    """Return the OS-appropriate state directory. Backend Spec §15.7."""
    match _platform():
        case Platform.MACOS:
            return _home() / "Library" / "Application Support" / APP_NAME / "state"
        case Platform.WINDOWS:
            return _env_path("LOCALAPPDATA", _home() / "AppData" / "Local") / APP_NAME / "state"
        case Platform.LINUX:
            return _env_path("XDG_STATE_HOME", _home() / ".local" / "state") / APP_NAME


def os_cache_path() -> Path:
    """Return the OS-appropriate cache directory. Backend Spec §7.2.4."""
    match _platform():
        case Platform.MACOS:
            return _home() / "Library" / "Caches" / APP_NAME
        case Platform.WINDOWS:
            return _env_path("LOCALAPPDATA", _home() / "AppData" / "Local") / APP_NAME / "Cache"
        case Platform.LINUX:
            return _env_path("XDG_CACHE_HOME", _home() / ".cache") / APP_NAME


def os_central_log_path() -> Path:
    """Return the OS-appropriate central log file. Backend Spec §16.3."""
    match _platform():
        case Platform.MACOS:
            return _home() / "Library" / "Logs" / APP_NAME / CENTRAL_LOG_FILE
        case Platform.WINDOWS:
            return (
                _env_path("LOCALAPPDATA", _home() / "AppData" / "Local")
                / APP_NAME
                / "Logs"
                / CENTRAL_LOG_FILE
            )
        case Platform.LINUX:
            return (
                _env_path("XDG_STATE_HOME", _home() / ".local" / "state")
                / APP_NAME
                / CENTRAL_LOG_FILE
            )


def default_orchestrator_staging_root() -> Path:
    """OS-conditional default for ``orchestrator.staging_root``. Backend Spec §9, §13."""
    if _platform() is Platform.WINDOWS:
        return _env_path("LOCALAPPDATA", _home() / "AppData" / "Local") / APP_NAME / "staging"
    return Path("/staging")


# ---------------------------------------------------------------------------
# Mkdir helpers (side effects)
# ---------------------------------------------------------------------------


def ensure_dir(path: Path) -> Path:
    """``mkdir -p`` the given path; return it. Idempotent."""
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_state_dir() -> Path:
    """``ensure_dir(os_state_path())``."""
    return ensure_dir(os_state_path())


def ensure_central_log_dir() -> Path:
    """``ensure_dir(os_central_log_path().parent)``."""
    return ensure_dir(os_central_log_path().parent)


# ---------------------------------------------------------------------------
# Equipment-ID canonicalization
# ---------------------------------------------------------------------------


def canonicalize_equipment_id(value: str) -> str:
    """Validate ``value`` against the §3.1 equipment-ID regex.

    Returns ``value`` unchanged on success. The §3.1 contract is "input
    must already be canonical" -- this function does NOT lowercase or
    otherwise mutate the input. It rejects, for example, ``confocal_01``
    outright rather than silently uppercasing it.

    Raises ``ConfigError`` naming the offending input on regex / length
    failure.
    """
    if not isinstance(value, str) or not value:
        msg = f"equipment_id must be a non-empty string; got {value!r}"
        raise ConfigError(msg)
    if len(value) > EQUIPMENT_ID_MAX_LENGTH:
        msg = (
            f"equipment_id {value!r} exceeds max length "
            f"{EQUIPMENT_ID_MAX_LENGTH} ({len(value)} chars)"
        )
        raise ConfigError(msg)
    if not EQUIPMENT_ID_PATTERN.fullmatch(value):
        msg = f"equipment_id {value!r} does not match {EQUIPMENT_ID_PATTERN.pattern}"
        raise ConfigError(msg)
    return value


# ---------------------------------------------------------------------------
# Run-path composition
# ---------------------------------------------------------------------------


def validate_project_short_id(value: str) -> str:
    """Validate ``value`` against ``PROJECT_SHORT_ID_PATTERN``.

    Returns the input unchanged. Raises ``ConfigError`` on mismatch.

    The short ID is a LIMS barcoding identifier recorded in project
    metadata (Backend Spec §3.2); it is no longer a path component, so
    path composition validates the project *name* instead.
    """
    if not isinstance(value, str) or not value:
        msg = f"project_short_id must be a non-empty string; got {value!r}"
        raise ConfigError(msg)
    if not PROJECT_SHORT_ID_PATTERN.fullmatch(value):
        msg = f"project_short_id {value!r} does not match {PROJECT_SHORT_ID_PATTERN.pattern}"
        raise ConfigError(msg)
    return value


def project_name_violations(value: str) -> list[tuple[str | None, str]]:
    """Return every way ``value`` fails the §3.2 project-name rule.

    The project folder is the human-readable LIMS name used verbatim,
    so it must be a safe single filesystem path segment. Each entry is
    a ``(matched_token, detail)`` pair -- ``matched_token`` is the
    offending character / reserved word (or ``None`` for whole-string
    failures). An empty list means the name is safe.

    Rejected: non-string / empty, over :data:`PROJECT_NAME_MAX_LENGTH`,
    leading or trailing whitespace, a reserved Windows device name, a
    trailing dot, any path separator or other Windows-illegal character,
    and any non-printable-ASCII character (control or non-ASCII). The
    name is never canonicalized -- a name that cannot be used verbatim
    must be renamed in the LIMS.
    """
    if not isinstance(value, str) or not value:
        return [(None, f"project name must be a non-empty string; got {value!r}")]

    problems: list[tuple[str | None, str]] = []
    if len(value) > PROJECT_NAME_MAX_LENGTH:
        problems.append(
            (
                None,
                f"project name {value!r} exceeds max length "
                f"{PROJECT_NAME_MAX_LENGTH} ({len(value)} chars)",
            )
        )
    if value != value.strip():
        problems.append((None, f"project name {value!r} has leading or trailing whitespace"))
    stem = value.split(".", 1)[0].strip().upper()
    if stem in WINDOWS_RESERVED_NAMES:
        problems.append((stem, f"project name {value!r} is a reserved Windows device name"))
    if value.endswith("."):
        problems.append(
            (".", f"project name {value!r} ends with a trailing dot, illegal on Windows targets")
        )
    seen: set[str] = set()
    for ch in value:
        if ch in seen:
            continue
        if not (0x20 <= ord(ch) <= 0x7E):
            seen.add(ch)
            problems.append(
                (
                    ch,
                    f"project name {value!r} contains non-ASCII or control character "
                    f"{ch!r}; project names must be printable ASCII",
                )
            )
        elif ch in WINDOWS_ILLEGAL_CHARS:
            seen.add(ch)
            problems.append(
                (ch, f"project name {value!r} contains illegal filesystem character {ch!r}")
            )
    return problems


def validate_project_name(value: str) -> str:
    """Validate ``value`` as a §3.2 project-folder name.

    Returns the input unchanged on success. Raises ``ConfigError``
    naming the first violation found by :func:`project_name_violations`.
    """
    problems = project_name_violations(value)
    if problems:
        raise ConfigError(problems[0][1])
    return value


def compose_run_path(
    *,
    local_root: Path,
    equipment_id: str,
    project_name: str,
    run_kind: RunKind,
    run_date: datetime,
) -> Path:
    """Compose the absolute on-disk path for a new run.

    Paths follow Backend Spec §3:

    - experimental: ``<local_root>/<EQUIPMENT_ID>/<project name>/Run_<DATE>/``
    - test:         ``<local_root>/<EQUIPMENT_ID>/<project name>/TestRuns/TestRun_<DATE>/``

    The ``<project name>`` segment is the human-readable LIMS name used
    verbatim (§3.2). Validates ``equipment_id`` via
    :func:`canonicalize_equipment_id` and ``project_name`` via
    :func:`validate_project_name`. ``run_date`` is stamped via
    ``run_date.strftime(RUN_DATE_STRFTIME)`` to produce the ISO 8601 leaf
    with colons replaced by hyphens.
    """
    canonicalize_equipment_id(equipment_id)
    validate_project_name(project_name)
    stamp = run_date.strftime(RUN_DATE_STRFTIME)
    project_dir = Path(local_root) / equipment_id / project_name
    if run_kind is RunKind.TEST:
        return project_dir / TEST_RUNS_DIR_NAME / f"{TEST_RUN_DIR_PREFIX}{stamp}"
    return project_dir / f"{RUN_DIR_PREFIX}{stamp}"


def compose_project_path(
    *,
    local_root: Path,
    equipment_id: str,
    project_name: str,
) -> Path:
    """Compose the project-level directory.

    ``<local_root>/<EQUIPMENT_ID>/<project name>/`` -- the project
    segment is the human-readable LIMS name used verbatim (§3.2).

    Validates ``equipment_id`` and ``project_name`` the same way
    :func:`compose_run_path` does.
    """
    canonicalize_equipment_id(equipment_id)
    validate_project_name(project_name)
    return Path(local_root) / equipment_id / project_name


# ---------------------------------------------------------------------------
# Setup-state evaluator (Backend Spec §4.9.1)
# ---------------------------------------------------------------------------


def _lims_slot_satisfied(
    config: Config,
    *,
    keyring_password_present: bool,
) -> bool:
    """Return True when the LIMS slot is configured.

    Spec §4.9.1: the slot is satisfied by EITHER (``endpoint`` non-empty AND
    ``email`` non-empty AND keyring has the password) OR
    (``offline_catalogue_path`` non-empty).
    """
    lims = config.lims
    if lims.offline_catalogue_path:
        return True
    return bool(lims.endpoint and lims.email and keyring_password_present)


def _paths_complete(config: Config) -> bool:
    """Return True when every required ``paths.*`` field is non-empty.

    The unit-level check is purely string emptiness -- filesystem
    accessibility is checked elsewhere in the §4.9.1 evaluation chain.
    """
    paths = config.paths
    return bool(paths.templates_dir and paths.plugin_dir and paths.local_root)


def evaluate_setup_state(
    config: Config | None,
    *,
    lims_reachable: bool = True,
    keyring_password_present: bool = True,
) -> SetupState:
    """Evaluate the §4.9.1 setup state.

    Order of gates (first-failing wins):

    1. ``config is None`` -> ``INCOMPLETE_NO_CONFIG``
    2. ``paths.templates_dir`` / ``plugin_dir`` / ``local_root`` any empty ->
       ``INCOMPLETE_MISSING_PATHS``
    3. equipment list empty -> ``INCOMPLETE_NO_EQUIPMENT``
    4. lims slot incomplete (no endpoint+email AND no offline_catalogue_path)
       -> ``INCOMPLETE_NO_LIMS``
    5. ``lims_reachable`` is ``False`` -> ``INCOMPLETE_LIMS_UNREACHABLE``
    6. otherwise -> ``READY``

    The ``lims_reachable`` flag is supplied by the caller from the
    ``LIMSClient.health_check()`` result. Default True so unit tests can
    skip the network call. The ``keyring_password_present`` flag stubs the
    keyring lookup so unit tests can exercise every branch without a real
    keyring backend.
    """
    if config is None:
        return SetupState.INCOMPLETE_NO_CONFIG
    if not _paths_complete(config):
        return SetupState.INCOMPLETE_MISSING_PATHS
    if not config.equipment:
        return SetupState.INCOMPLETE_NO_EQUIPMENT
    if not _lims_slot_satisfied(config, keyring_password_present=keyring_password_present):
        return SetupState.INCOMPLETE_NO_LIMS
    if not lims_reachable:
        return SetupState.INCOMPLETE_LIMS_UNREACHABLE
    return SetupState.READY


def setup_state_missing(state: SetupState, config: Config | None) -> list[dict[str, str]]:
    """Translate a state into ``{field, reason}`` dicts for ``/api/v1/setup/status``.

    Backend Spec §4.9.3. Returns ``[]`` when the state is ``READY`` or
    ``INCOMPLETE_LIMS_UNREACHABLE`` (the soft-block state surfaces a
    banner, not a missing-field list).
    """
    if state in (SetupState.READY, SetupState.INCOMPLETE_LIMS_UNREACHABLE):
        return []
    if state is SetupState.INCOMPLETE_NO_CONFIG:
        return [{"field": "config.yaml", "reason": "missing"}]
    if state is SetupState.INCOMPLETE_NO_EQUIPMENT:
        return [{"field": "equipment", "reason": "empty"}]
    if state is SetupState.INCOMPLETE_MISSING_PATHS:
        return _missing_paths_fields(config)
    if state is SetupState.INCOMPLETE_NO_LIMS:
        return _missing_lims_fields(config)
    return []


def _missing_paths_fields(config: Config | None) -> list[dict[str, str]]:
    field_specs = (
        ("paths.templates_dir", lambda c: c.paths.templates_dir),
        ("paths.plugin_dir", lambda c: c.paths.plugin_dir),
        ("paths.local_root", lambda c: c.paths.local_root),
    )
    if config is None:
        return [{"field": name, "reason": "unset"} for name, _ in field_specs]
    return [
        {"field": name, "reason": "unset"} for name, accessor in field_specs if not accessor(config)
    ]


def _missing_lims_fields(config: Config | None) -> list[dict[str, str]]:
    if config is None:
        return [{"field": "lims", "reason": "unset"}]
    missing: list[dict[str, str]] = []
    has_endpoint = bool(config.lims.endpoint)
    has_email = bool(config.lims.email)
    if not has_endpoint:
        missing.append({"field": "lims.endpoint", "reason": "unset"})
    if not has_email:
        missing.append({"field": "lims.email", "reason": "unset"})
    # The keyring-password slot is only flagged when the live-LIMS branch is
    # otherwise plausible (endpoint and email both filled in). Otherwise the
    # operator hasn't started filling in LIMS yet, and prompting them about
    # a missing keyring password is misleading.
    if has_endpoint and has_email and not config.lims.offline_catalogue_path:
        missing.append({"field": "lims.password", "reason": "missing_in_keyring"})
    return missing


def setup_state_next_action(state: SetupState) -> SetupNextAction | None:
    """Map a state to the §4.9.3 next-action enum member.

    Returns ``None`` when the state is :class:`SetupState.READY`
    (no further action required).
    """
    match state:
        case SetupState.INCOMPLETE_NO_CONFIG | SetupState.INCOMPLETE_MISSING_PATHS:
            return SetupNextAction.SET_PATHS
        case SetupState.INCOMPLETE_NO_EQUIPMENT:
            return SetupNextAction.ADD_EQUIPMENT
        case SetupState.INCOMPLETE_NO_LIMS:
            return SetupNextAction.CONFIGURE_LIMS
        case SetupState.INCOMPLETE_LIMS_UNREACHABLE:
            return SetupNextAction.TEST_LIMS
        case SetupState.READY:
            return None


# ---------------------------------------------------------------------------
# Run-/project-cache subpath helpers (Backend Spec §11.3, §11.4, §13.4)
# ---------------------------------------------------------------------------


def cache_dir(run_or_project_dir: Path) -> Path:
    """Return the ``.exlab-wizard/`` subdirectory for a run or project root."""
    return run_or_project_dir / CACHE_DIR_NAME


def creation_json_path(run_or_project_dir: Path) -> Path:
    """Return the ``creation.json`` path under a run or project directory."""
    return cache_dir(run_or_project_dir) / CREATION_JSON_NAME


def ingest_json_path(run_dir: Path) -> Path:
    """Return the ``ingest.json`` path under a run directory."""
    return cache_dir(run_dir) / INGEST_JSON_NAME


def equipment_json_path(equipment_dir: Path) -> Path:
    """Return the ``equipment.json`` path under an equipment directory."""
    return cache_dir(equipment_dir) / EQUIPMENT_JSON_NAME


def readme_fields_json_path(run_or_project_dir: Path) -> Path:
    """Return the ``readme_fields.json`` path under a run or project directory."""
    return cache_dir(run_or_project_dir) / README_FIELDS_JSON_NAME


# ---------------------------------------------------------------------------
# Run-directory name classifiers
# ---------------------------------------------------------------------------


def is_run_dir(name: str) -> bool:
    """True if ``name`` is an experimental-run directory.

    Note: ``RUN_DIR_PREFIX`` is ``Run_`` and ``TEST_RUN_DIR_PREFIX`` is
    ``TestRun_``; ``"TestRun_X"`` does NOT start with ``"Run_"``, so the
    two classifiers are mutually exclusive.
    """
    return name.startswith(RUN_DIR_PREFIX)


def is_test_run_dir(name: str) -> bool:
    """True if ``name`` is a test-run directory."""
    return name.startswith(TEST_RUN_DIR_PREFIX)


def run_dir_stem(stamp: str, *, test: bool = False) -> str:
    """Return ``Run_<stamp>`` or ``TestRun_<stamp>``."""
    prefix = TEST_RUN_DIR_PREFIX if test else RUN_DIR_PREFIX
    return f"{prefix}{stamp}"
