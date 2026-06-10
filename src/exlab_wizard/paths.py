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
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from exlab_wizard.constants import (
    APP_NAME,
    CACHE_DIR_NAME,
    CENTRAL_LOG_FILE,
    CREATION_JSON_NAME,
    DISPLAY_NAME,
    EQUIPMENT_ID_MAX_LENGTH,
    EQUIPMENT_ID_PATTERN,
    EQUIPMENT_JSON_NAME,
    PROJECT_NAME_MAX_LENGTH,
    PROJECT_SHORT_ID_PATTERN,
    README_FIELDS_JSON_NAME,
    RUN_DATE_STRFTIME,
    RUN_DIR_PREFIX,
    RUNS_DIR_NAME,
    TEST_MODE_ENV,
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
    "TEST_MODE_ENV",
    "app_root_writable",
    "cache_dir",
    "canonicalize_equipment_id",
    "compose_project_path",
    "compose_run_path",
    "creation_json_path",
    "default_app_root",
    "ensure_app_dirs",
    "ensure_central_log_dir",
    "ensure_dir",
    "ensure_state_dir",
    "equipment_json_path",
    "evaluate_setup_state",
    "is_run_dir",
    "is_test_run_dir",
    "os_cache_path",
    "os_central_log_path",
    "os_config_path",
    "os_documents_path",
    "os_state_path",
    "readme_fields_json_path",
    "run_dir_stem",
    "setup_state_missing",
    "setup_state_next_action",
    "suggested_staging_root",
    "validate_project_short_id",
]


# ---------------------------------------------------------------------------
# Test-mode app-name override
# ---------------------------------------------------------------------------
# Setting ``EXLAB_WIZARD_TEST_MODE=1`` swaps APP_NAME for ``APP_NAME-test``
# in every OS-path helper below, redirecting config / state / cache / logs
# into a parallel ``exlab-wizard-test`` sandbox without touching real user
# directories. The same env var also drives ``apply_test_mode_prefix`` in
# ``config.loader`` so on-disk + NAS run directories sort under a
# ``TEST_<id>/...`` namespace. The env var (rather than a CLI arg threaded
# through every layer) means the window subprocess spawned by
# WindowLauncher inherits the override automatically. See
# ``exlab-wizard-tray --test``.
#
# ``TEST_MODE_ENV`` is re-exported here for backward compat with callers
# that imported it from this module before it was centralized in
# ``constants/app.py``.


def _app_name() -> str:
    """``APP_NAME`` (suffixed ``-test`` when ``EXLAB_WIZARD_TEST_MODE=1``)."""
    if os.environ.get(TEST_MODE_ENV) == "1":
        return f"{APP_NAME}-test"
    return APP_NAME


def _display_name() -> str:
    """``DISPLAY_NAME`` (suffixed ``-test`` when ``EXLAB_WIZARD_TEST_MODE=1``).

    The operator-facing Documents-subfolder name, mirroring ``_app_name``'s
    test-mode handling so a ``--test`` run sandboxes into
    ``<Documents>/ExLabWizard-test`` rather than the real working tree.
    """
    if os.environ.get(TEST_MODE_ENV) == "1":
        return f"{DISPLAY_NAME}-test"
    return DISPLAY_NAME


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
    name = _app_name()
    match _platform():
        case Platform.MACOS:
            return _home() / "Library" / "Application Support" / name / "config.yaml"
        case Platform.WINDOWS:
            return _env_path("APPDATA", _home() / "AppData" / "Roaming") / name / "config.yaml"
        case Platform.LINUX:
            return _env_path("XDG_CONFIG_HOME", _home() / ".config") / name / "config.yaml"


def os_state_path() -> Path:
    """Return the OS-appropriate state directory. Backend Spec §15.7."""
    name = _app_name()
    match _platform():
        case Platform.MACOS:
            return _home() / "Library" / "Application Support" / name / "state"
        case Platform.WINDOWS:
            return _env_path("LOCALAPPDATA", _home() / "AppData" / "Local") / name / "state"
        case Platform.LINUX:
            return _env_path("XDG_STATE_HOME", _home() / ".local" / "state") / name


def os_cache_path() -> Path:
    """Return the OS-appropriate cache directory. Backend Spec §7.2.4."""
    name = _app_name()
    match _platform():
        case Platform.MACOS:
            return _home() / "Library" / "Caches" / name
        case Platform.WINDOWS:
            return _env_path("LOCALAPPDATA", _home() / "AppData" / "Local") / name / "Cache"
        case Platform.LINUX:
            return _env_path("XDG_CACHE_HOME", _home() / ".cache") / name


def os_central_log_path() -> Path:
    """Return the OS-appropriate central log file. Backend Spec §16.3."""
    name = _app_name()
    match _platform():
        case Platform.MACOS:
            return _home() / "Library" / "Logs" / name / CENTRAL_LOG_FILE
        case Platform.WINDOWS:
            return (
                _env_path("LOCALAPPDATA", _home() / "AppData" / "Local")
                / name
                / "Logs"
                / CENTRAL_LOG_FILE
            )
        case Platform.LINUX:
            return (
                _env_path("XDG_STATE_HOME", _home() / ".local" / "state") / name / CENTRAL_LOG_FILE
            )


def suggested_staging_root() -> Path:
    """Suggested (not default) ``orchestrator.staging_root``. Backend Spec §9, §13.

    ``staging_root`` is opt-in: blank means this device is not a staging PC
    and nothing is created. This helper only supplies the greyed *placeholder*
    shown in Settings to guide an operator who chooses to opt in -- it is pure
    and side-effect-free, never written and never ``mkdir``'d. A directory is
    created only when the operator saves a non-empty path (see
    ``ui.mount._persist_config``).

    Staged runs are bulk experiment data relayed through this device on their
    way to the NAS, so the suggestion lives under an ``exlab-wizard/`` app
    folder on every platform -- mirroring config / state / cache -- rather than
    a bare ``/staging`` mount. On Linux it follows ``XDG_DATA_HOME`` (bulk user
    data, not transient cache) so an un-synced run is never treated as
    discardable.
    """
    name = _app_name()
    match _platform():
        case Platform.MACOS:
            return _home() / "Library" / "Application Support" / name / "staging"
        case Platform.WINDOWS:
            return _env_path("LOCALAPPDATA", _home() / "AppData" / "Local") / name / "staging"
        case Platform.LINUX:
            return _env_path("XDG_DATA_HOME", _home() / ".local" / "share") / name / "staging"


def os_documents_path() -> Path:
    """Return the operator's OS *Documents* directory. Pure; never raises.

    The single app root (:func:`default_app_root`) lives under Documents so
    everything an operator curates -- experiment data, templates, plugins --
    sits in a familiar, backup-friendly location (mirroring how other desktop
    apps adopt the user's Documents folder), distinct from the hidden
    config / state / cache dirs named by :func:`_app_name`.

    Per platform:

    - **macOS** -- ``~/Documents``.
    - **Windows** -- the Known Folder for Documents via
      ``SHGetKnownFolderPath(FOLDERID_Documents)`` so a relocated or localized
      Documents folder is honoured; on any ``ctypes`` failure it falls back to
      ``%USERPROFILE%\\Documents`` and finally ``~/Documents``.
    - **Linux** -- ``$XDG_DOCUMENTS_DIR`` if set, else ``~/Documents``.
    """
    match _platform():
        case Platform.MACOS:
            return _home() / "Documents"
        case Platform.WINDOWS:
            return _windows_documents_path()
        case Platform.LINUX:
            return _env_path("XDG_DOCUMENTS_DIR", _home() / "Documents")


def _windows_documents_path() -> Path:
    """Resolve the Windows Documents Known Folder, with graceful fallbacks.

    Tries ``SHGetKnownFolderPath(FOLDERID_Documents)`` so a user who relocated
    their Documents folder (or runs a localized Windows) gets the real path;
    falls back to ``%USERPROFILE%\\Documents`` then ``~/Documents`` if the
    Win32 call is unavailable or errors.
    """
    fallback = _env_path("USERPROFILE", _home()) / "Documents"
    try:
        return _shget_known_documents()
    except Exception:
        # Non-Windows host (no ``ctypes.windll``) or a failed/empty Win32 call.
        return fallback


def _shget_known_documents() -> Path:  # pragma: no cover -- Windows-only Known Folder API
    """Resolve ``FOLDERID_Documents`` via ``SHGetKnownFolderPath`` (Windows only).

    Raises on any non-Windows host (``ctypes.windll`` is undefined there) or on a
    failed / empty Win32 result, so :func:`_windows_documents_path` falls back.
    Excluded from coverage: the Win32 call cannot execute on the Linux/macOS CI
    runners.
    """
    import ctypes
    from ctypes import windll, wintypes  # type: ignore[attr-defined]

    # FOLDERID_Documents = {FDD39AD0-238F-46AF-ADB4-6C85480369C7}
    class _GUID(ctypes.Structure):
        _fields_ = (
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_byte * 8),
        )

    folderid = _GUID(
        0xFDD39AD0,
        0x238F,
        0x46AF,
        (ctypes.c_byte * 8)(0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7),
    )
    out = ctypes.c_wchar_p()
    # SHGetKnownFolderPath returns S_OK (0) on success; any non-zero HRESULT is a
    # failure, so raise rather than read an unset/garbage pointer.
    if windll.shell32.SHGetKnownFolderPath(ctypes.byref(folderid), 0, None, ctypes.byref(out)):
        raise OSError("SHGetKnownFolderPath failed")
    try:
        if not out.value:
            raise OSError("SHGetKnownFolderPath returned an empty path")
        return Path(out.value)
    finally:
        windll.ole32.CoTaskMemFree(out)


def default_app_root() -> Path:
    """Suggested default app root under the OS *Documents* folder.

    ``<Documents>/ExLabWizard`` (``ExLabWizard-test`` in test mode). This is the
    single configurable root from which ``templates/``, ``plugins/`` and
    ``data/`` are derived (see :class:`exlab_wizard.config.models.PathsConfig`).
    Pure and side-effect-free; the directory tree is materialized only by
    :func:`ensure_app_dirs`.
    """
    return os_documents_path() / _display_name()


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


def ensure_app_dirs(config: Config) -> None:
    """``mkdir -p`` the app root and its derived working subdirectories.

    Materializes ``app_root`` plus the derived ``data/``, ``templates/`` and
    ``plugins/`` folders (see
    :class:`exlab_wizard.config.models.PathsConfig`). Idempotent; called on
    tray bring-up and after a Settings save so a fresh install never has to
    pre-create its working tree by hand. The subfolder names come from the
    config's derived properties so this stays the single creation site.
    """
    paths = config.paths
    for directory in (paths.app_root, paths.data_root, paths.templates_dir, paths.plugin_dir):
        ensure_dir(Path(directory))


def app_root_writable(config: Config) -> bool:
    """Return True when the app root and ``data/`` are creatable and writable.

    Drives the §4.9.1 paths gate (see :func:`evaluate_setup_state`): the app
    root is always populated (it defaults under Documents), so the gate no
    longer asks "is it blank" but "can we actually create and write runs
    here". Attempts :func:`ensure_app_dirs`, then probes ``os.access(W_OK)``
    on the app root and the data root. Returns False on any ``OSError`` (bad
    drive, permission denied) rather than raising.
    """
    try:
        ensure_app_dirs(config)
    except OSError:
        return False
    return os.access(config.paths.app_root, os.W_OK) and os.access(config.paths.data_root, os.W_OK)


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

    Paths follow Backend Spec §3 with the GUI/Orchestrator Redesign §3.4
    ``Runs/`` symmetry update:

    - experimental: ``<local_root>/<EQUIPMENT_ID>/<project name>/Runs/Run_<DATE>/``
    - test:         ``<local_root>/<EQUIPMENT_ID>/<project name>/TestRuns/TestRun_<DATE>/``

    The ``<project name>`` segment is the human-readable LIMS name used
    verbatim (§3.2). Validates ``equipment_id`` via
    :func:`canonicalize_equipment_id` and ``project_name`` via
    :func:`validate_project_name`. ``run_date`` is stamped via
    ``run_date.strftime(RUN_DATE_STRFTIME)``; the redesign drops seconds
    in favour of minute precision, so two runs created on the same
    instrument within the same minute resolve to the same path. Copier's
    ``overwrite=False`` (User Interaction Spec §5, gate 6) rejects the
    second creation rather than clobbering — v1 surfaces this as a hard
    failure the operator retries.
    """
    canonicalize_equipment_id(equipment_id)
    validate_project_name(project_name)
    stamp = run_date.strftime(RUN_DATE_STRFTIME)
    project_dir = Path(local_root) / equipment_id / project_name
    if run_kind is RunKind.TEST:
        return project_dir / TEST_RUNS_DIR_NAME / f"{TEST_RUN_DIR_PREFIX}{stamp}"
    return project_dir / RUNS_DIR_NAME / f"{RUN_DIR_PREFIX}{stamp}"


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


def _nas_in_use(config: Config) -> bool:
    """True when at least one nas-mode equipment exists (NAS sync is active)."""
    from exlab_wizard.constants import SyncMode

    return any(eq.sync_mode == SyncMode.NAS for eq in config.equipment)


def _nas_remote_satisfied(config: Config, *, nas_remote_available: Callable[[str], bool]) -> bool:
    """True when the nas: remote is configured AND present in rclone.conf.

    Only gates when NAS sync is actually in use (a stage-only device with no
    nas-mode equipment does not need a NAS remote).
    """
    if not _nas_in_use(config):
        return True
    remote = config.nas.remote
    return bool(remote) and nas_remote_available(remote)


def evaluate_setup_state(
    config: Config | None,
    *,
    lims_reachable: bool = True,
    keyring_password_present: bool = True,
    nas_remote_available: Callable[[str], bool] | None = None,
    paths_writable: bool = True,
) -> SetupState:
    """Evaluate the §4.9.1 setup state.

    Order of gates (first-failing wins):

    1. ``config is None`` -> ``INCOMPLETE_NO_CONFIG``
    2. ``paths.app_root`` cannot be created / written ->
       ``INCOMPLETE_PATHS_UNWRITABLE``
    3. equipment list empty -> ``INCOMPLETE_NO_EQUIPMENT``
    4. NAS sync is in use but the ``nas:`` remote is unset or absent from
       rclone.conf -> ``INCOMPLETE_NO_NAS_REMOTE`` (rclone.conf migration)
    5. lims slot incomplete (no endpoint+email AND no offline_catalogue_path)
       -> ``INCOMPLETE_NO_LIMS``
    6. ``lims_reachable`` is ``False`` -> ``INCOMPLETE_LIMS_UNREACHABLE``
    7. otherwise -> ``READY``

    The ``lims_reachable`` flag is supplied by the caller from the
    ``LIMSClient.health_check()`` result. Default True so unit tests can
    skip the network call. The ``keyring_password_present`` flag stubs the
    keyring lookup so unit tests can exercise every branch without a real
    keyring backend. ``nas_remote_available`` answers "is this rclone
    remote present in rclone.conf?"; it defaults to "always True" so
    callers and tests that don't care about the NAS gate behave as before.
    ``paths_writable`` answers "can the app root be created and written?"
    (computed by the caller via :func:`app_root_writable`); it defaults True
    so callers/tests that don't care about the paths gate behave as before.
    The app root always defaults under the OS Documents folder, so the gate
    checks writability rather than emptiness.
    """
    if config is None:
        return SetupState.INCOMPLETE_NO_CONFIG
    if not paths_writable:
        return SetupState.INCOMPLETE_PATHS_UNWRITABLE
    if not _orchestrator_identity_complete(config):
        return SetupState.INCOMPLETE_NO_ORCHESTRATOR
    if not config.equipment:
        return SetupState.INCOMPLETE_NO_EQUIPMENT
    remote_lookup = nas_remote_available if nas_remote_available is not None else (lambda _n: True)
    if not _nas_remote_satisfied(config, nas_remote_available=remote_lookup):
        return SetupState.INCOMPLETE_NO_NAS_REMOTE
    if not _lims_slot_satisfied(config, keyring_password_present=keyring_password_present):
        return SetupState.INCOMPLETE_NO_LIMS
    if not lims_reachable:
        return SetupState.INCOMPLETE_LIMS_UNREACHABLE
    return SetupState.READY


def _orchestrator_identity_complete(config: Config) -> bool:
    """Return True when this device has an orchestrator label.

    Only ``label`` is required -- it is stamped into every run's
    ``creation.json`` as the workstation identity, independent of staging.
    ``staging_root`` is opt-in (a blank value just means this device is not a
    staging PC), so it no longer gates setup.
    """
    return bool(config.orchestrator.label)


def setup_state_missing(
    state: SetupState,
    config: Config | None,
) -> list[dict[str, str]]:
    """Translate a state into ``{field, reason}`` dicts for ``/api/v1/setup/status``.

    Backend Spec §4.9.3. Returns ``[]`` when the state is ``READY`` or
    ``INCOMPLETE_LIMS_UNREACHABLE`` (the soft-block state surfaces a
    banner, not a missing-field list). When ``state`` is
    ``INCOMPLETE_NO_NAS_REMOTE`` the missing list names ``nas.remote``
    with a reason describing whether it is unset or absent from
    rclone.conf (the state itself is enough for the UI to deep-link to
    the setup docs).
    """
    match state:
        case SetupState.READY | SetupState.INCOMPLETE_LIMS_UNREACHABLE:
            return []
        case SetupState.INCOMPLETE_NO_CONFIG:
            return [{"field": "config.yaml", "reason": "missing"}]
        case SetupState.INCOMPLETE_NO_EQUIPMENT:
            return [{"field": "equipment", "reason": "empty"}]
        case SetupState.INCOMPLETE_PATHS_UNWRITABLE:
            return _missing_paths_fields()
        case SetupState.INCOMPLETE_NO_ORCHESTRATOR:
            return _missing_orchestrator_fields(config)
        case SetupState.INCOMPLETE_NO_NAS_REMOTE:
            return _missing_nas_fields(config)
        case SetupState.INCOMPLETE_NO_LIMS:
            return _missing_lims_fields(config)
    # Defensive fallback: an unrecognized state (e.g. a future enum member or a
    # non-SetupState passed by a misbehaving caller) yields no missing-field
    # rows rather than ``None``, honouring the ``list[...]`` return contract.
    return []


def _missing_nas_fields(config: Config | None) -> list[dict[str, str]]:
    """Missing-field row(s) for ``INCOMPLETE_NO_NAS_REMOTE``.

    Distinguishes an unset ``nas.remote`` (shared by both transports)
    from the per-transport "configured but unavailable" reasons: the
    named remote absent from rclone.conf, or (rsync_ssh) the pinned
    ssh identity file not found on disk.
    """
    if config is None or not config.nas.remote:
        return [{"field": "nas.remote", "reason": "unset"}]
    from exlab_wizard.constants import SyncTransport

    if config.nas.transport == SyncTransport.RSYNC_SSH:
        return [
            {"field": "nas.ssh_identity_file", "reason": "identity_file_missing"}
        ]
    return [{"field": "nas.remote", "reason": "not_found_in_rclone_conf"}]


def _missing_orchestrator_fields(config: Config | None) -> list[dict[str, str]]:
    """Only ``label`` is required; ``staging_root`` is opt-in (see gate)."""
    if config is None:
        return [{"field": "orchestrator.label", "reason": "missing"}]
    out: list[dict[str, str]] = []
    if not config.orchestrator.label:
        out.append({"field": "orchestrator.label", "reason": "missing"})
    return out


def _missing_paths_fields() -> list[dict[str, str]]:
    """Single ``paths.app_root`` row for ``INCOMPLETE_PATHS_UNWRITABLE``.

    The app root always defaults under Documents, so the failure is never
    "unset" -- it is that the resolved location cannot be created or written
    (missing drive, permission denied).
    """
    return [{"field": "paths.app_root", "reason": "unwritable"}]


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
        case SetupState.INCOMPLETE_NO_CONFIG | SetupState.INCOMPLETE_PATHS_UNWRITABLE:
            return SetupNextAction.SET_PATHS
        case SetupState.INCOMPLETE_NO_ORCHESTRATOR:
            # Redesign §3.1: label + staging_root fold into an early
            # Settings section. SET_PATHS routes to that section.
            return SetupNextAction.SET_PATHS
        case SetupState.INCOMPLETE_NO_EQUIPMENT:
            return SetupNextAction.ADD_EQUIPMENT
        case SetupState.INCOMPLETE_NO_NAS_REMOTE:
            return SetupNextAction.CONFIGURE_RCLONE_REMOTE
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
