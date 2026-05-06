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
    CENTRAL_LOG_FILE,
    EQUIPMENT_ID_MAX_LENGTH,
    EQUIPMENT_ID_PATTERN,
    PROJECT_SHORT_ID_PATTERN,
    RUN_DATE_STRFTIME,
    RUN_DIR_PREFIX,
    TEST_RUN_DIR_PREFIX,
    TEST_RUNS_DIR_NAME,
    RunKind,
    SetupState,
)
from exlab_wizard.errors import ConfigError
from exlab_wizard.logging import get_logger

if TYPE_CHECKING:
    from exlab_wizard.config.models import Config

__all__ = [
    "canonicalize_equipment_id",
    "compose_project_path",
    "compose_run_path",
    "default_orchestrator_staging_root",
    "ensure_central_log_dir",
    "ensure_dir",
    "ensure_state_dir",
    "evaluate_setup_state",
    "os_cache_path",
    "os_central_log_path",
    "os_config_path",
    "os_state_path",
    "setup_state_missing",
    "setup_state_next_action",
]

_log = get_logger(__name__)

# App identifier used in OS-appropriate paths (stable across platforms).
_APP_NAME = "exlab-wizard"


# ---------------------------------------------------------------------------
# OS-aware path helpers (no side effects)
# ---------------------------------------------------------------------------


def _home() -> Path:
    """Return the operator's home directory.

    Centralized so test fixtures can monkeypatch ``Path.home`` once and have
    every helper see the override.
    """
    return Path.home()


def _is_macos() -> bool:
    return sys.platform == "darwin"


def _is_windows() -> bool:
    return sys.platform == "win32"


def os_config_path() -> Path:
    """Return the OS-appropriate path of ``config.yaml``. Backend Spec §9.

    macOS:   ``~/Library/Application Support/exlab-wizard/config.yaml``
    Windows: ``%APPDATA%/exlab-wizard/config.yaml``
    Linux:   ``$XDG_CONFIG_HOME/exlab-wizard/config.yaml`` or
             ``~/.config/exlab-wizard/config.yaml``
    """
    if _is_macos():
        return _home() / "Library" / "Application Support" / _APP_NAME / "config.yaml"
    if _is_windows():
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else _home() / "AppData" / "Roaming"
        return base / _APP_NAME / "config.yaml"
    # Linux / POSIX fallback.
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else _home() / ".config"
    return base / _APP_NAME / "config.yaml"


def os_state_path() -> Path:
    """Return the OS-appropriate state directory. Backend Spec §15.7.

    macOS:   ``~/Library/Application Support/exlab-wizard/state``
    Windows: ``%LOCALAPPDATA%/exlab-wizard/state``
    Linux:   ``$XDG_STATE_HOME/exlab-wizard`` or ``~/.local/state/exlab-wizard``
    """
    if _is_macos():
        return _home() / "Library" / "Application Support" / _APP_NAME / "state"
    if _is_windows():
        local = os.environ.get("LOCALAPPDATA")
        base = Path(local) if local else _home() / "AppData" / "Local"
        return base / _APP_NAME / "state"
    xdg = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg) if xdg else _home() / ".local" / "state"
    return base / _APP_NAME


def os_cache_path() -> Path:
    """Return the OS-appropriate cache directory. Backend Spec §7.2.4.

    macOS:   ``~/Library/Caches/exlab-wizard``
    Windows: ``%LOCALAPPDATA%/exlab-wizard/Cache``
    Linux:   ``$XDG_CACHE_HOME/exlab-wizard`` or ``~/.cache/exlab-wizard``
    """
    if _is_macos():
        return _home() / "Library" / "Caches" / _APP_NAME
    if _is_windows():
        local = os.environ.get("LOCALAPPDATA")
        base = Path(local) if local else _home() / "AppData" / "Local"
        return base / _APP_NAME / "Cache"
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg) if xdg else _home() / ".cache"
    return base / _APP_NAME


def os_central_log_path() -> Path:
    """Return the OS-appropriate central log file. Backend Spec §16.3.

    macOS:   ``~/Library/Logs/exlab-wizard/app.log``
    Windows: ``%LOCALAPPDATA%/exlab-wizard/Logs/app.log``
    Linux:   ``${XDG_STATE_HOME:-~/.local/state}/exlab-wizard/app.log``
    """
    if _is_macos():
        return _home() / "Library" / "Logs" / _APP_NAME / CENTRAL_LOG_FILE
    if _is_windows():
        local = os.environ.get("LOCALAPPDATA")
        base = Path(local) if local else _home() / "AppData" / "Local"
        return base / _APP_NAME / "Logs" / CENTRAL_LOG_FILE
    xdg = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg) if xdg else _home() / ".local" / "state"
    return base / _APP_NAME / CENTRAL_LOG_FILE


def default_orchestrator_staging_root() -> Path:
    """OS-conditional default for ``orchestrator.staging_root``. Backend Spec §9, §13.

    macOS/Linux: ``/staging``
    Windows:     ``%LOCALAPPDATA%/exlab-wizard/staging``
    """
    if _is_windows():
        local = os.environ.get("LOCALAPPDATA")
        base = Path(local) if local else _home() / "AppData" / "Local"
        return base / _APP_NAME / "staging"
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


def _validate_project_short_id(value: str) -> str:
    """Validate ``value`` against ``PROJECT_SHORT_ID_PATTERN``.

    Returns the input unchanged. Raises ``ConfigError`` on mismatch.
    """
    if not isinstance(value, str) or not value:
        msg = f"project_short_id must be a non-empty string; got {value!r}"
        raise ConfigError(msg)
    if not PROJECT_SHORT_ID_PATTERN.fullmatch(value):
        msg = f"project_short_id {value!r} does not match {PROJECT_SHORT_ID_PATTERN.pattern}"
        raise ConfigError(msg)
    return value


def compose_run_path(
    *,
    local_root: Path,
    equipment_id: str,
    project_short_id: str,
    run_kind: RunKind,
    run_date: datetime,
) -> Path:
    """Compose the absolute on-disk path for a new run.

    Paths follow Backend Spec §3:

    - experimental: ``<local_root>/<EQUIPMENT_ID>/<PROJ-NNNN>/Run_<DATE>/``
    - test:         ``<local_root>/<EQUIPMENT_ID>/<PROJ-NNNN>/TestRuns/TestRun_<DATE>/``

    Validates ``equipment_id`` via :func:`canonicalize_equipment_id` and
    ``project_short_id`` via ``PROJECT_SHORT_ID_PATTERN``. ``run_date`` is
    stamped via ``run_date.strftime(RUN_DATE_STRFTIME)`` to produce the
    ISO 8601 leaf with colons replaced by hyphens.
    """
    canonicalize_equipment_id(equipment_id)
    _validate_project_short_id(project_short_id)
    stamp = run_date.strftime(RUN_DATE_STRFTIME)
    project_dir = Path(local_root) / equipment_id / project_short_id
    if run_kind is RunKind.TEST:
        return project_dir / TEST_RUNS_DIR_NAME / f"{TEST_RUN_DIR_PREFIX}{stamp}"
    return project_dir / f"{RUN_DIR_PREFIX}{stamp}"


def compose_project_path(
    *,
    local_root: Path,
    equipment_id: str,
    project_short_id: str,
) -> Path:
    """Compose the project-level directory.

    ``<local_root>/<EQUIPMENT_ID>/<PROJ-NNNN>/``.

    Validates ``equipment_id`` and ``project_short_id`` the same way
    :func:`compose_run_path` does.
    """
    canonicalize_equipment_id(equipment_id)
    _validate_project_short_id(project_short_id)
    return Path(local_root) / equipment_id / project_short_id


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
    offline = (lims.offline_catalogue_path or "").strip()
    if offline:
        return True
    endpoint = (lims.endpoint or "").strip()
    email = (lims.email or "").strip()
    return bool(endpoint and email and keyring_password_present)


def _paths_complete(config: Config) -> bool:
    """Return True when every required ``paths.*`` field is non-empty.

    The unit-level check is purely string emptiness -- filesystem
    accessibility is checked elsewhere in the §4.9.1 evaluation chain.
    """
    paths = config.paths
    templates = (paths.templates_dir or "").strip()
    plugin = (paths.plugin_dir or "").strip()
    local_root = (paths.local_root or "").strip()
    return bool(templates and plugin and local_root)


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
    missing: list[dict[str, str]] = []
    if state is SetupState.INCOMPLETE_MISSING_PATHS:
        if config is None:
            return [
                {"field": "paths.templates_dir", "reason": "unset"},
                {"field": "paths.plugin_dir", "reason": "unset"},
                {"field": "paths.local_root", "reason": "unset"},
            ]
        if not (config.paths.templates_dir or "").strip():
            missing.append({"field": "paths.templates_dir", "reason": "unset"})
        if not (config.paths.plugin_dir or "").strip():
            missing.append({"field": "paths.plugin_dir", "reason": "unset"})
        if not (config.paths.local_root or "").strip():
            missing.append({"field": "paths.local_root", "reason": "unset"})
        return missing
    if state is SetupState.INCOMPLETE_NO_EQUIPMENT:
        return [{"field": "equipment", "reason": "empty"}]
    if state is SetupState.INCOMPLETE_NO_LIMS:
        # No usable LIMS source: neither (endpoint+email+keyring) nor offline catalogue.
        if config is None:
            return [{"field": "lims", "reason": "unset"}]
        if not (config.lims.endpoint or "").strip():
            missing.append({"field": "lims.endpoint", "reason": "unset"})
        if not (config.lims.email or "").strip():
            missing.append({"field": "lims.email", "reason": "unset"})
        if not (config.lims.offline_catalogue_path or "").strip():
            missing.append({"field": "lims.password", "reason": "missing_in_keyring"})
        return missing
    return missing


def setup_state_next_action(state: SetupState) -> str | None:
    """Map a state to the §4.9.3 ``next_action`` string.

    - ``INCOMPLETE_NO_CONFIG`` -> ``"set_paths"``
    - ``INCOMPLETE_MISSING_PATHS`` -> ``"set_paths"``
    - ``INCOMPLETE_NO_EQUIPMENT`` -> ``"add_equipment"``
    - ``INCOMPLETE_NO_LIMS`` -> ``"configure_lims"``
    - ``INCOMPLETE_LIMS_UNREACHABLE`` -> ``"test_lims"``
    - ``READY`` -> ``None``
    """
    match state:
        case SetupState.INCOMPLETE_NO_CONFIG | SetupState.INCOMPLETE_MISSING_PATHS:
            return "set_paths"
        case SetupState.INCOMPLETE_NO_EQUIPMENT:
            return "add_equipment"
        case SetupState.INCOMPLETE_NO_LIMS:
            return "configure_lims"
        case SetupState.INCOMPLETE_LIMS_UNREACHABLE:
            return "test_lims"
        case SetupState.READY:
            return None
