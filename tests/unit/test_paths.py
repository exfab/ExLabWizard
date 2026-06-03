"""Unit tests for ``exlab_wizard.paths``.

OS-aware directory helpers + equipment-id canonicalization + setup-state
evaluator. Backend Spec §3.1, §4.9, §9, §15, §16.

Each OS branch is exercised by monkeypatching ``sys.platform`` and (for
Linux/Windows) the relevant XDG / APPDATA env vars. Filesystem-touching
helpers use pytest's ``tmp_path`` fixture. The setup-state evaluator
tests construct minimal Pydantic ``Config`` instances using Agent A's
public surface.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from exlab_wizard.config.models import (
    Config,
    EquipmentConfig,
    LIMSConfig,
    PathsConfig,
)
from exlab_wizard.constants import RunKind, SetupNextAction, SetupState
from exlab_wizard.errors import ConfigError
from exlab_wizard.paths import (
    app_root_writable,
    canonicalize_equipment_id,
    compose_project_path,
    compose_run_path,
    default_app_root,
    ensure_app_dirs,
    ensure_central_log_dir,
    ensure_dir,
    ensure_state_dir,
    evaluate_setup_state,
    os_cache_path,
    os_central_log_path,
    os_config_path,
    os_documents_path,
    os_state_path,
    project_name_violations,
    setup_state_missing,
    setup_state_next_action,
    suggested_staging_root,
    validate_project_name,
)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_equipment(equipment_id: str = "CONFOCAL_01") -> EquipmentConfig:
    """Construct a minimal valid EquipmentConfig for setup-state fixtures."""
    return EquipmentConfig.model_validate(
        {
            "id": equipment_id,
            "label": "Confocal Microscope",
            "nas_root": "//nas01/lab",
        }
    )


def _make_orchestrator():  # type: ignore[no-untyped-def]
    """Minimal orchestrator identity (Redesign §3.1) for setup-state fixtures."""
    from exlab_wizard.config.models import OrchestratorConfig

    return OrchestratorConfig(
        label="Lab Acquisition Station 01",
        staging_root="/staging",
    )


def _ready_config() -> Config:
    """Construct a fully READY Config (paths + equipment + LIMS endpoint+email).

    Redesign §3.1: orchestrator.label + orchestrator.staging_root are
    now required even in the always-on world; the README field is set
    here so the setup-state evaluator returns READY.
    """
    from exlab_wizard.config.models import NasConfig, OrchestratorConfig

    return Config(
        paths=PathsConfig(app_root="/srv/exlab"),
        lims=LIMSConfig(endpoint="https://lims.example/api/v1", email="op@lab.example"),
        equipment=[_make_equipment()],
        orchestrator=OrchestratorConfig(
            label="Lab Acquisition Station 01",
            staging_root="/staging",
        ),
        # rclone.conf NAS-sync migration: nas-mode equipment requires a
        # configured ``nas.remote`` for the setup gate to read READY (the
        # default ``nas_remote_available`` answers "always available").
        nas=NasConfig(remote="nas01", base_root="/srv/nas"),
    )


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Return a fake HOME under tmp_path; route ``Path.home()`` to it."""
    home = tmp_path / "home" / "operator"
    home.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    # Strip any inherited XDG / APPDATA so each test asserts its own state.
    for var in (
        "XDG_CONFIG_HOME",
        "XDG_STATE_HOME",
        "XDG_CACHE_HOME",
        "XDG_DATA_HOME",
        "XDG_DOCUMENTS_DIR",
        "APPDATA",
        "LOCALAPPDATA",
        "USERPROFILE",
    ):
        monkeypatch.delenv(var, raising=False)
    return home


# ---------------------------------------------------------------------------
# os_config_path
# ---------------------------------------------------------------------------


def test_os_config_path_macos(monkeypatch: pytest.MonkeyPatch, fake_home: Path) -> None:
    monkeypatch.setattr("sys.platform", "darwin")
    expected = fake_home / "Library" / "Application Support" / "exlab-wizard" / "config.yaml"
    assert os_config_path() == expected


def test_os_config_path_windows(monkeypatch: pytest.MonkeyPatch, fake_home: Path) -> None:
    monkeypatch.setattr("sys.platform", "win32")
    appdata = fake_home / "AppData" / "Roaming"
    monkeypatch.setenv("APPDATA", str(appdata))
    expected = appdata / "exlab-wizard" / "config.yaml"
    assert os_config_path() == expected


def test_os_config_path_linux_with_xdg(monkeypatch: pytest.MonkeyPatch, fake_home: Path) -> None:
    monkeypatch.setattr("sys.platform", "linux")
    xdg = fake_home / "xdg-config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    expected = xdg / "exlab-wizard" / "config.yaml"
    assert os_config_path() == expected


def test_os_config_path_linux_without_xdg(monkeypatch: pytest.MonkeyPatch, fake_home: Path) -> None:
    monkeypatch.setattr("sys.platform", "linux")
    expected = fake_home / ".config" / "exlab-wizard" / "config.yaml"
    assert os_config_path() == expected


# ---------------------------------------------------------------------------
# os_state_path
# ---------------------------------------------------------------------------


def test_os_state_path_macos(monkeypatch: pytest.MonkeyPatch, fake_home: Path) -> None:
    monkeypatch.setattr("sys.platform", "darwin")
    expected = fake_home / "Library" / "Application Support" / "exlab-wizard" / "state"
    assert os_state_path() == expected


def test_os_state_path_windows(monkeypatch: pytest.MonkeyPatch, fake_home: Path) -> None:
    monkeypatch.setattr("sys.platform", "win32")
    local = fake_home / "AppData" / "Local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    expected = local / "exlab-wizard" / "state"
    assert os_state_path() == expected


def test_os_state_path_linux_with_xdg(monkeypatch: pytest.MonkeyPatch, fake_home: Path) -> None:
    monkeypatch.setattr("sys.platform", "linux")
    xdg = fake_home / "xdg-state"
    monkeypatch.setenv("XDG_STATE_HOME", str(xdg))
    expected = xdg / "exlab-wizard"
    assert os_state_path() == expected


def test_os_state_path_linux_without_xdg(monkeypatch: pytest.MonkeyPatch, fake_home: Path) -> None:
    monkeypatch.setattr("sys.platform", "linux")
    expected = fake_home / ".local" / "state" / "exlab-wizard"
    assert os_state_path() == expected


# ---------------------------------------------------------------------------
# os_cache_path
# ---------------------------------------------------------------------------


def test_os_cache_path_macos(monkeypatch: pytest.MonkeyPatch, fake_home: Path) -> None:
    monkeypatch.setattr("sys.platform", "darwin")
    expected = fake_home / "Library" / "Caches" / "exlab-wizard"
    assert os_cache_path() == expected


def test_os_cache_path_windows(monkeypatch: pytest.MonkeyPatch, fake_home: Path) -> None:
    monkeypatch.setattr("sys.platform", "win32")
    local = fake_home / "AppData" / "Local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    expected = local / "exlab-wizard" / "Cache"
    assert os_cache_path() == expected


def test_os_cache_path_linux_with_xdg(monkeypatch: pytest.MonkeyPatch, fake_home: Path) -> None:
    monkeypatch.setattr("sys.platform", "linux")
    xdg = fake_home / "xdg-cache"
    monkeypatch.setenv("XDG_CACHE_HOME", str(xdg))
    expected = xdg / "exlab-wizard"
    assert os_cache_path() == expected


def test_os_cache_path_linux_without_xdg(monkeypatch: pytest.MonkeyPatch, fake_home: Path) -> None:
    monkeypatch.setattr("sys.platform", "linux")
    expected = fake_home / ".cache" / "exlab-wizard"
    assert os_cache_path() == expected


# ---------------------------------------------------------------------------
# os_central_log_path
# ---------------------------------------------------------------------------


def test_os_central_log_path_macos(monkeypatch: pytest.MonkeyPatch, fake_home: Path) -> None:
    monkeypatch.setattr("sys.platform", "darwin")
    expected = fake_home / "Library" / "Logs" / "exlab-wizard" / "app.log"
    assert os_central_log_path() == expected


def test_os_central_log_path_windows(monkeypatch: pytest.MonkeyPatch, fake_home: Path) -> None:
    monkeypatch.setattr("sys.platform", "win32")
    local = fake_home / "AppData" / "Local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    expected = local / "exlab-wizard" / "Logs" / "app.log"
    assert os_central_log_path() == expected


def test_os_central_log_path_linux_with_xdg(
    monkeypatch: pytest.MonkeyPatch, fake_home: Path
) -> None:
    monkeypatch.setattr("sys.platform", "linux")
    xdg = fake_home / "xdg-state"
    monkeypatch.setenv("XDG_STATE_HOME", str(xdg))
    expected = xdg / "exlab-wizard" / "app.log"
    assert os_central_log_path() == expected


def test_os_central_log_path_linux_without_xdg(
    monkeypatch: pytest.MonkeyPatch, fake_home: Path
) -> None:
    monkeypatch.setattr("sys.platform", "linux")
    expected = fake_home / ".local" / "state" / "exlab-wizard" / "app.log"
    assert os_central_log_path() == expected


# ---------------------------------------------------------------------------
# suggested_staging_root
# ---------------------------------------------------------------------------
#
# staging_root is opt-in; this helper only supplies the Settings placeholder.
# It must never return a bare ``/staging`` -- on every platform it nests under
# an ``exlab-wizard/`` app folder, mirroring config / state / cache.


def test_suggested_staging_root_linux_fallback(
    monkeypatch: pytest.MonkeyPatch, fake_home: Path
) -> None:
    monkeypatch.setattr("sys.platform", "linux")
    expected = fake_home / ".local" / "share" / "exlab-wizard" / "staging"
    assert suggested_staging_root() == expected


def test_suggested_staging_root_linux_honors_xdg_data_home(
    monkeypatch: pytest.MonkeyPatch, fake_home: Path
) -> None:
    monkeypatch.setattr("sys.platform", "linux")
    xdg = fake_home / "xdg-data"
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg))
    assert suggested_staging_root() == xdg / "exlab-wizard" / "staging"


def test_suggested_staging_root_macos(monkeypatch: pytest.MonkeyPatch, fake_home: Path) -> None:
    monkeypatch.setattr("sys.platform", "darwin")
    expected = fake_home / "Library" / "Application Support" / "exlab-wizard" / "staging"
    assert suggested_staging_root() == expected


def test_suggested_staging_root_windows(monkeypatch: pytest.MonkeyPatch, fake_home: Path) -> None:
    monkeypatch.setattr("sys.platform", "win32")
    local = fake_home / "AppData" / "Local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    expected = local / "exlab-wizard" / "staging"
    assert suggested_staging_root() == expected


# ---------------------------------------------------------------------------
# os_documents_path / default_app_root  (the Documents-based app root)
# ---------------------------------------------------------------------------


def test_os_documents_path_macos(monkeypatch: pytest.MonkeyPatch, fake_home: Path) -> None:
    monkeypatch.setattr("sys.platform", "darwin")
    assert os_documents_path() == fake_home / "Documents"


def test_os_documents_path_linux_with_xdg(monkeypatch: pytest.MonkeyPatch, fake_home: Path) -> None:
    monkeypatch.setattr("sys.platform", "linux")
    xdg = fake_home / "xdg-docs"
    monkeypatch.setenv("XDG_DOCUMENTS_DIR", str(xdg))
    assert os_documents_path() == xdg


def test_os_documents_path_linux_without_xdg(
    monkeypatch: pytest.MonkeyPatch, fake_home: Path
) -> None:
    monkeypatch.setattr("sys.platform", "linux")
    assert os_documents_path() == fake_home / "Documents"


def test_os_documents_path_windows_userprofile_fallback(
    monkeypatch: pytest.MonkeyPatch, fake_home: Path
) -> None:
    """On a non-Windows test runner the ctypes Known-Folder call is
    unavailable, so the resolver falls back to ``%USERPROFILE%\\Documents``.
    (The SHGetKnownFolderPath success path is exercised only on real Windows.)
    """
    monkeypatch.setattr("sys.platform", "win32")
    profile = fake_home / "winprofile"
    monkeypatch.setenv("USERPROFILE", str(profile))
    assert os_documents_path() == profile / "Documents"


def test_os_documents_path_windows_home_fallback(
    monkeypatch: pytest.MonkeyPatch, fake_home: Path
) -> None:
    """With neither the Known Folder nor USERPROFILE available, fall back to
    ``~/Documents``."""
    monkeypatch.setattr("sys.platform", "win32")
    assert os_documents_path() == fake_home / "Documents"


def test_default_app_root_macos(monkeypatch: pytest.MonkeyPatch, fake_home: Path) -> None:
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.delenv("EXLAB_WIZARD_TEST_MODE", raising=False)
    assert default_app_root() == fake_home / "Documents" / "ExLabWizard"


def test_default_app_root_test_mode_suffix(
    monkeypatch: pytest.MonkeyPatch, fake_home: Path
) -> None:
    """Test mode sandboxes the Documents subfolder so tests never write into
    the operator's real ``ExLabWizard`` tree."""
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setenv("EXLAB_WIZARD_TEST_MODE", "1")
    assert default_app_root() == fake_home / "Documents" / "ExLabWizard-test"


# ---------------------------------------------------------------------------
# ensure_app_dirs / app_root_writable
# ---------------------------------------------------------------------------


def _app_root_config(app_root: Path) -> Config:
    """Minimal Config carrying just the app root (for app-dir helpers)."""
    return Config(paths=PathsConfig(app_root=str(app_root)))


def test_ensure_app_dirs_creates_derived_tree(tmp_path: Path) -> None:
    app_root = tmp_path / "ExLabWizard"
    ensure_app_dirs(_app_root_config(app_root))
    assert app_root.is_dir()
    assert (app_root / "data").is_dir()
    assert (app_root / "templates").is_dir()
    assert (app_root / "plugins").is_dir()


def test_ensure_app_dirs_idempotent(tmp_path: Path) -> None:
    config = _app_root_config(tmp_path / "ExLabWizard")
    ensure_app_dirs(config)
    ensure_app_dirs(config)  # second call is a no-op, must not raise
    assert (tmp_path / "ExLabWizard" / "data").is_dir()


def test_app_root_writable_true_for_writable_tree(tmp_path: Path) -> None:
    assert app_root_writable(_app_root_config(tmp_path / "ExLabWizard")) is True


def test_app_root_writable_false_when_uncreatable(tmp_path: Path) -> None:
    """A path whose parent is a file (so mkdir fails) is reported unwritable."""
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    assert app_root_writable(_app_root_config(blocker / "ExLabWizard")) is False


# ---------------------------------------------------------------------------
# Test-mode suffix (EXLAB_WIZARD_TEST_MODE=1)
# ---------------------------------------------------------------------------
#
# When ``EXLAB_WIZARD_TEST_MODE=1`` is set the OS-path helpers must direct
# every root into a parallel ``exlab-wizard-test`` sandbox, so
# ``exlab-wizard-tray --test`` exercises features without touching the
# operator's real config / state / cache / log directories. One test per
# helper per platform branch would be overkill; one test per helper on a
# representative platform plus a Windows spot-check on the orchestrator
# default covers the wiring.


def test_test_mode_suffixes_os_config_path(
    monkeypatch: pytest.MonkeyPatch, fake_home: Path
) -> None:
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setenv("EXLAB_WIZARD_TEST_MODE", "1")
    expected = fake_home / "Library" / "Application Support" / "exlab-wizard-test" / "config.yaml"
    assert os_config_path() == expected


def test_test_mode_suffixes_os_state_path(monkeypatch: pytest.MonkeyPatch, fake_home: Path) -> None:
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setenv("EXLAB_WIZARD_TEST_MODE", "1")
    expected = fake_home / ".local" / "state" / "exlab-wizard-test"
    assert os_state_path() == expected


def test_test_mode_suffixes_os_cache_path(monkeypatch: pytest.MonkeyPatch, fake_home: Path) -> None:
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setenv("EXLAB_WIZARD_TEST_MODE", "1")
    expected = fake_home / ".cache" / "exlab-wizard-test"
    assert os_cache_path() == expected


def test_test_mode_suffixes_os_central_log_path(
    monkeypatch: pytest.MonkeyPatch, fake_home: Path
) -> None:
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setenv("EXLAB_WIZARD_TEST_MODE", "1")
    expected = fake_home / "Library" / "Logs" / "exlab-wizard-test" / "app.log"
    assert os_central_log_path() == expected


def test_test_mode_suffixes_suggested_staging_root(
    monkeypatch: pytest.MonkeyPatch, fake_home: Path
) -> None:
    """The suggestion now nests under APP_NAME on every platform, so the
    test-mode suffix applies on POSIX too (not just Windows)."""
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setenv("EXLAB_WIZARD_TEST_MODE", "1")
    expected = fake_home / "Library" / "Application Support" / "exlab-wizard-test" / "staging"
    assert suggested_staging_root() == expected


def test_test_mode_off_does_not_suffix(monkeypatch: pytest.MonkeyPatch, fake_home: Path) -> None:
    """Unset / non-'1' values must leave the real OS dirs untouched."""
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.delenv("EXLAB_WIZARD_TEST_MODE", raising=False)
    assert os_config_path() == (
        fake_home / "Library" / "Application Support" / "exlab-wizard" / "config.yaml"
    )
    monkeypatch.setenv("EXLAB_WIZARD_TEST_MODE", "0")
    # The contract is strict equality to "1"; anything else falls back to APP_NAME.
    assert os_config_path() == (
        fake_home / "Library" / "Application Support" / "exlab-wizard" / "config.yaml"
    )


# ---------------------------------------------------------------------------
# ensure_dir / ensure_state_dir / ensure_central_log_dir
# ---------------------------------------------------------------------------


def test_ensure_dir_creates_missing(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b" / "c"
    assert not target.exists()
    result = ensure_dir(target)
    assert result == target
    assert target.is_dir()


def test_ensure_dir_idempotent_when_exists(tmp_path: Path) -> None:
    target = tmp_path / "already-here"
    target.mkdir()
    # Calling twice must not raise; second call is a no-op.
    ensure_dir(target)
    ensure_dir(target)
    assert target.is_dir()


def test_ensure_state_dir_creates_state_dir(
    monkeypatch: pytest.MonkeyPatch, fake_home: Path
) -> None:
    monkeypatch.setattr("sys.platform", "linux")
    state = ensure_state_dir()
    assert state.is_dir()
    assert state == os_state_path()


def test_ensure_central_log_dir_creates_parent(
    monkeypatch: pytest.MonkeyPatch, fake_home: Path
) -> None:
    monkeypatch.setattr("sys.platform", "linux")
    parent = ensure_central_log_dir()
    assert parent.is_dir()
    assert parent == os_central_log_path().parent


# ---------------------------------------------------------------------------
# canonicalize_equipment_id
# ---------------------------------------------------------------------------


def test_canonicalize_equipment_id_accepts_canonical() -> None:
    assert canonicalize_equipment_id("CONFOCAL_01") == "CONFOCAL_01"


@pytest.mark.parametrize(
    "valid",
    ["CONFOCAL_01", "FLOW_01", "NANOPORE_03", "LIGHTSHEET_2A", "X", "A1"],
)
def test_canonicalize_equipment_id_accepts_other_canonical_examples(valid: str) -> None:
    assert canonicalize_equipment_id(valid) == valid


def test_canonicalize_equipment_id_rejects_lowercase() -> None:
    with pytest.raises(ConfigError):
        canonicalize_equipment_id("confocal_01")


def test_canonicalize_equipment_id_rejects_hyphen() -> None:
    with pytest.raises(ConfigError):
        canonicalize_equipment_id("CONFOCAL-01")


def test_canonicalize_equipment_id_rejects_leading_digit() -> None:
    with pytest.raises(ConfigError):
        canonicalize_equipment_id("01_CONFOCAL")


def test_canonicalize_equipment_id_rejects_space() -> None:
    with pytest.raises(ConfigError):
        canonicalize_equipment_id("CONFOCAL 01")


def test_canonicalize_equipment_id_rejects_non_ascii() -> None:
    with pytest.raises(ConfigError):
        canonicalize_equipment_id("仪器_01")


def test_canonicalize_equipment_id_rejects_too_long() -> None:
    too_long = "A" * 33
    with pytest.raises(ConfigError) as exc:
        canonicalize_equipment_id(too_long)
    assert "33" in str(exc.value)
    assert "max length 32" in str(exc.value) or "32" in str(exc.value)


def test_canonicalize_equipment_id_accepts_max_length() -> None:
    at_limit = "A" * 32
    assert canonicalize_equipment_id(at_limit) == at_limit


def test_canonicalize_equipment_id_rejects_empty() -> None:
    with pytest.raises(ConfigError):
        canonicalize_equipment_id("")


def test_canonicalize_equipment_id_rejects_none() -> None:
    """Non-str input hits the isinstance guard, not the regex."""
    with pytest.raises(ConfigError) as info:
        canonicalize_equipment_id(None)  # type: ignore[arg-type]
    assert "non-empty string" in str(info.value)


def test_canonicalize_equipment_id_rejects_int() -> None:
    """Non-str input hits the isinstance guard, not the length / regex check."""
    with pytest.raises(ConfigError) as info:
        canonicalize_equipment_id(42)  # type: ignore[arg-type]
    assert "non-empty string" in str(info.value)
    assert "42" in str(info.value)


def test_canonicalize_equipment_id_does_not_mutate_input() -> None:
    """Spec contract: input must already be canonical; no silent uppercasing."""
    with pytest.raises(ConfigError):
        canonicalize_equipment_id("Confocal_01")


# ---------------------------------------------------------------------------
# compose_run_path / compose_project_path
# ---------------------------------------------------------------------------


_RUN_DATE = datetime(2026, 4, 17, 14, 32, 0)
# Redesign §3.4: minute precision (seconds dropped).
_EXPECTED_STAMP = "2026-04-17T14-32"
# The <project>/ segment is the human-readable LIMS name, used verbatim. §3.2.
_PROJECT_NAME = "Cortex Q3 Pilot"


def test_compose_run_path_experimental(tmp_path: Path) -> None:
    result = compose_run_path(
        local_root=tmp_path,
        equipment_id="CONFOCAL_01",
        project_name=_PROJECT_NAME,
        run_kind=RunKind.EXPERIMENTAL,
        run_date=_RUN_DATE,
    )
    expected = tmp_path / "CONFOCAL_01" / _PROJECT_NAME / "Runs" / f"Run_{_EXPECTED_STAMP}"
    assert result == expected


def test_compose_run_path_test(tmp_path: Path) -> None:
    result = compose_run_path(
        local_root=tmp_path,
        equipment_id="CONFOCAL_01",
        project_name=_PROJECT_NAME,
        run_kind=RunKind.TEST,
        run_date=_RUN_DATE,
    )
    expected = tmp_path / "CONFOCAL_01" / _PROJECT_NAME / "TestRuns" / f"TestRun_{_EXPECTED_STAMP}"
    assert result == expected


def test_compose_run_path_strftime_format() -> None:
    """The leaf is ISO 8601 with colons replaced by hyphens. §3.1.

    Redesign §3.4: minute precision; seconds are dropped.
    """
    when = datetime(2030, 12, 31, 23, 59, 59)
    result = compose_run_path(
        local_root=Path("/data/lab"),
        equipment_id="FLOW_01",
        project_name=_PROJECT_NAME,
        run_kind=RunKind.EXPERIMENTAL,
        run_date=when,
    )
    assert result.name == "Run_2030-12-31T23-59"
    assert ":" not in result.name


def test_compose_run_path_experimental_under_runs_subdir() -> None:
    """Redesign §3.4: experimental runs live under <project>/Runs/Run_*."""
    result = compose_run_path(
        local_root=Path("/data/lab"),
        equipment_id="FLOW_01",
        project_name=_PROJECT_NAME,
        run_kind=RunKind.EXPERIMENTAL,
        run_date=_RUN_DATE,
    )
    assert result.parent.name == "Runs"
    assert result.parent.parent.name == _PROJECT_NAME


def test_compose_run_path_same_minute_collision_returns_same_path() -> None:
    """Redesign §3.4: two runs in the same minute resolve to the same
    path (Copier overwrite=False rejects the second creation)."""
    first = compose_run_path(
        local_root=Path("/data/lab"),
        equipment_id="FLOW_01",
        project_name=_PROJECT_NAME,
        run_kind=RunKind.EXPERIMENTAL,
        run_date=datetime(2026, 4, 17, 14, 32, 5),
    )
    second = compose_run_path(
        local_root=Path("/data/lab"),
        equipment_id="FLOW_01",
        project_name=_PROJECT_NAME,
        run_kind=RunKind.EXPERIMENTAL,
        run_date=datetime(2026, 4, 17, 14, 32, 55),
    )
    assert first == second
    assert first.name == "Run_2026-04-17T14-32"


def test_compose_run_path_rejects_invalid_equipment_id(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        compose_run_path(
            local_root=tmp_path,
            equipment_id="confocal_01",
            project_name=_PROJECT_NAME,
            run_kind=RunKind.EXPERIMENTAL,
            run_date=_RUN_DATE,
        )


def test_compose_run_path_rejects_unsafe_project_name(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        compose_run_path(
            local_root=tmp_path,
            equipment_id="CONFOCAL_01",
            project_name="bad/name",
            run_kind=RunKind.EXPERIMENTAL,
            run_date=_RUN_DATE,
        )


def test_compose_project_path_basic(tmp_path: Path) -> None:
    result = compose_project_path(
        local_root=tmp_path,
        equipment_id="CONFOCAL_01",
        project_name=_PROJECT_NAME,
    )
    expected = tmp_path / "CONFOCAL_01" / _PROJECT_NAME
    assert result == expected


def test_compose_project_path_rejects_invalid_equipment_id(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        compose_project_path(
            local_root=tmp_path,
            equipment_id="bad id",
            project_name=_PROJECT_NAME,
        )


def test_compose_project_path_rejects_unsafe_project_name(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        compose_project_path(
            local_root=tmp_path,
            equipment_id="CONFOCAL_01",
            project_name="NUL",
        )


def test_compose_run_path_rejects_non_str_project_name(tmp_path: Path) -> None:
    """A non-str project_name hits the isinstance guard."""
    with pytest.raises(ConfigError) as info:
        compose_run_path(
            local_root=tmp_path,
            equipment_id="CONFOCAL_01",
            project_name=None,  # type: ignore[arg-type]
            run_kind=RunKind.EXPERIMENTAL,
            run_date=_RUN_DATE,
        )
    assert "non-empty string" in str(info.value)


def test_compose_run_path_rejects_empty_project_name(tmp_path: Path) -> None:
    """An empty-string project_name also hits the same guard."""
    with pytest.raises(ConfigError) as info:
        compose_run_path(
            local_root=tmp_path,
            equipment_id="CONFOCAL_01",
            project_name="",
            run_kind=RunKind.EXPERIMENTAL,
            run_date=_RUN_DATE,
        )
    assert "non-empty string" in str(info.value)


def test_compose_project_path_rejects_non_str_project_name(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as info:
        compose_project_path(
            local_root=tmp_path,
            equipment_id="CONFOCAL_01",
            project_name=42,  # type: ignore[arg-type]
        )
    assert "non-empty string" in str(info.value)


# ---------------------------------------------------------------------------
# validate_project_name / project_name_violations -- §3.2 safe-segment rule
# ---------------------------------------------------------------------------


def test_validate_project_name_accepts_human_readable_names() -> None:
    """Spaces and ordinary punctuation are fine -- the name is used verbatim."""
    for name in ("Cortex Q3 Pilot", "PROJ-0042", "Q3_run (pilot)", "a.b.c"):
        assert validate_project_name(name) == name
        assert project_name_violations(name) == []


@pytest.mark.parametrize(
    "name",
    [
        "",
        "bad/name",
        "back\\slash",
        " leading",
        "trailing ",
        "trailing.",
        "ctrl\ttab",
        "café",  # non-ASCII
        "CON",  # reserved Windows device name
        "nul.txt",  # reserved stem, case-insensitive
        "star*",
        'quote"',
    ],
)
def test_validate_project_name_rejects_unsafe_names(name: str) -> None:
    with pytest.raises(ConfigError):
        validate_project_name(name)
    assert project_name_violations(name)


def test_validate_project_name_rejects_overlong_name() -> None:
    with pytest.raises(ConfigError) as info:
        validate_project_name("x" * 256)
    assert "max length" in str(info.value)


def test_validate_project_name_does_not_mutate_input() -> None:
    """The §3.2 contract is 'used verbatim' -- no canonicalisation."""
    name = "Cortex Q3 Pilot"
    assert validate_project_name(name) is name


# ---------------------------------------------------------------------------
# evaluate_setup_state
# ---------------------------------------------------------------------------


def test_evaluate_setup_state_no_config() -> None:
    assert evaluate_setup_state(None) is SetupState.INCOMPLETE_NO_CONFIG


def test_evaluate_setup_state_paths_unwritable() -> None:
    """An unwritable app root trips the paths gate (right after no-config)."""
    config = Config(
        paths=PathsConfig(app_root="/srv/exlab"),
        equipment=[_make_equipment()],
        orchestrator=_make_orchestrator(),
    )
    assert (
        evaluate_setup_state(config, paths_writable=False) is SetupState.INCOMPLETE_PATHS_UNWRITABLE
    )


def test_evaluate_setup_state_paths_writable_defaults_true() -> None:
    """``paths_writable`` defaults True, so the gate is skipped unless asked."""
    config = Config(
        paths=PathsConfig(app_root="/srv/exlab"),
        equipment=[_make_equipment()],
        orchestrator=_make_orchestrator(),
    )
    # Default leaves the paths gate satisfied; the chain moves past it (the
    # next unsatisfied gate here is the LIMS slot, not the paths gate).
    assert evaluate_setup_state(config) is not SetupState.INCOMPLETE_PATHS_UNWRITABLE


def test_evaluate_setup_state_no_equipment() -> None:
    config = Config(
        paths=PathsConfig(app_root="/srv/exlab"),
        equipment=[],
        orchestrator=_make_orchestrator(),
    )
    assert evaluate_setup_state(config) is SetupState.INCOMPLETE_NO_EQUIPMENT


def test_evaluate_setup_state_no_orchestrator() -> None:
    """Only ``label`` gates orchestrator identity; a blank label trips it."""
    config = Config(
        paths=PathsConfig(app_root="/srv/exlab"),
        equipment=[_make_equipment()],
    )
    assert evaluate_setup_state(config) is SetupState.INCOMPLETE_NO_ORCHESTRATOR


def test_evaluate_setup_state_no_orchestrator_when_only_staging_set() -> None:
    """A staging root without a label still trips -- staging never substitutes."""
    from exlab_wizard.config.models import OrchestratorConfig

    config = Config(
        paths=PathsConfig(app_root="/srv/exlab"),
        equipment=[_make_equipment()],
        orchestrator=OrchestratorConfig(label="", staging_root="/srv/staging"),
    )
    assert evaluate_setup_state(config) is SetupState.INCOMPLETE_NO_ORCHESTRATOR


def test_evaluate_setup_state_blank_staging_root_is_allowed() -> None:
    """staging_root is opt-in: a blank value does not block READY."""
    from exlab_wizard.config.models import NasConfig, OrchestratorConfig

    config = Config(
        paths=PathsConfig(app_root="/srv/exlab"),
        lims=LIMSConfig(endpoint="https://lims.example/api/v1", email="op@lab.example"),
        equipment=[_make_equipment()],
        orchestrator=OrchestratorConfig(label="Lab Acquisition Station 01", staging_root=""),
        nas=NasConfig(remote="nas01", base_root="/srv/nas"),
    )
    assert evaluate_setup_state(config) is SetupState.READY


def test_setup_state_missing_for_no_orchestrator_lists_only_label() -> None:
    """The orchestrator missing-field rollup no longer mentions staging_root."""
    from exlab_wizard.config.models import OrchestratorConfig

    config = Config(
        paths=PathsConfig(app_root="/srv/exlab"),
        equipment=[_make_equipment()],
        orchestrator=OrchestratorConfig(label="", staging_root=""),
    )
    missing = setup_state_missing(SetupState.INCOMPLETE_NO_ORCHESTRATOR, config)
    assert missing == [{"field": "orchestrator.label", "reason": "missing"}]


def test_evaluate_setup_state_no_lims() -> None:
    from exlab_wizard.config.models import NasConfig

    config = Config(
        paths=PathsConfig(app_root="/srv/exlab"),
        lims=LIMSConfig(endpoint="", email="", offline_catalogue_path=""),
        equipment=[_make_equipment()],
        orchestrator=_make_orchestrator(),
        nas=NasConfig(remote="nas01", base_root="/srv/nas"),
    )
    assert evaluate_setup_state(config) is SetupState.INCOMPLETE_NO_LIMS


def test_evaluate_setup_state_lims_via_offline_catalogue() -> None:
    """Offline catalogue path satisfies the LIMS slot without endpoint+email."""
    from exlab_wizard.config.models import NasConfig

    config = Config(
        paths=PathsConfig(app_root="/srv/exlab"),
        lims=LIMSConfig(
            endpoint="",
            email="",
            offline_catalogue_path="/mnt/share/offline_catalogue.json",
        ),
        equipment=[_make_equipment()],
        orchestrator=_make_orchestrator(),
        nas=NasConfig(remote="nas01", base_root="/srv/nas"),
    )
    # Even with keyring missing, offline catalogue path makes the slot complete.
    assert evaluate_setup_state(config, keyring_password_present=False) is SetupState.READY


def test_evaluate_setup_state_lims_unreachable() -> None:
    config = _ready_config()
    assert (
        evaluate_setup_state(config, lims_reachable=False) is SetupState.INCOMPLETE_LIMS_UNREACHABLE
    )


def test_evaluate_setup_state_ready() -> None:
    assert evaluate_setup_state(_ready_config()) is SetupState.READY


def test_evaluate_setup_state_keyring_missing() -> None:
    """endpoint+email present but keyring lacks the password -> INCOMPLETE_NO_LIMS."""
    config = _ready_config()
    assert (
        evaluate_setup_state(config, keyring_password_present=False)
        is SetupState.INCOMPLETE_NO_LIMS
    )


def _nas_remote_config(remote: str = "") -> Config:
    """Build a READY-shaped config with a single nas-mode equipment.

    NAS sync is in use (nas-mode equipment present), so the rclone-remote
    gate applies. ``remote`` is the configured ``nas.remote`` name.
    NOTE: nas-mode ``EquipmentConfig`` still requires a ``transport`` block
    in this phase (removed in Phase 7), hence the transport dict.
    """
    from exlab_wizard.config.models import NasConfig

    return Config(
        paths={"app_root": "/srv/exlab"},
        orchestrator={"label": "ws-1"},
        equipment=[
            EquipmentConfig(
                id="EQ_01",
                label="Eq",
                nas_root="//n/x",
            )
        ],
        lims={"endpoint": "https://x", "email": "a@b.c"},
        nas=NasConfig(remote=remote, base_root="/srv"),
    )


def test_setup_incomplete_when_nas_remote_blank() -> None:
    """NAS sync in use but no remote configured -> INCOMPLETE_NO_NAS_REMOTE."""
    assert (
        evaluate_setup_state(_nas_remote_config(remote=""), nas_remote_available=lambda n: True)
        == SetupState.INCOMPLETE_NO_NAS_REMOTE
    )


def test_setup_incomplete_when_remote_not_in_rclone_conf() -> None:
    """Remote named but absent from rclone.conf -> INCOMPLETE_NO_NAS_REMOTE."""
    assert (
        evaluate_setup_state(
            _nas_remote_config(remote="nas01"), nas_remote_available=lambda n: False
        )
        == SetupState.INCOMPLETE_NO_NAS_REMOTE
    )


def test_setup_ready_when_remote_present() -> None:
    """Remote named and present in rclone.conf -> READY."""
    assert (
        evaluate_setup_state(
            _nas_remote_config(remote="nas01"), nas_remote_available=lambda n: True
        )
        == SetupState.READY
    )


def test_evaluate_setup_state_nas_state_precedes_lims_state() -> None:
    """A configured LIMS does not mask the missing NAS remote."""
    config = _nas_remote_config(remote="")
    state = evaluate_setup_state(
        config,
        keyring_password_present=False,
        nas_remote_available=lambda _n: True,
    )
    # NAS slot is checked before LIMS in the gate order.
    assert state is SetupState.INCOMPLETE_NO_NAS_REMOTE


def test_setup_state_missing_for_no_nas_remote_reports_remote_field() -> None:
    """The missing list names ``nas.remote`` and distinguishes unset vs absent."""
    from exlab_wizard.paths import setup_state_missing

    unset = setup_state_missing(SetupState.INCOMPLETE_NO_NAS_REMOTE, _nas_remote_config(remote=""))
    assert unset == [{"field": "nas.remote", "reason": "unset"}]

    absent = setup_state_missing(
        SetupState.INCOMPLETE_NO_NAS_REMOTE, _nas_remote_config(remote="nas01")
    )
    assert absent == [{"field": "nas.remote", "reason": "not_found_in_rclone_conf"}]


def test_setup_state_next_action_for_no_nas_remote() -> None:
    from exlab_wizard.paths import setup_state_next_action

    assert (
        setup_state_next_action(SetupState.INCOMPLETE_NO_NAS_REMOTE)
        is SetupNextAction.CONFIGURE_RCLONE_REMOTE
    )


def test_evaluate_setup_state_endpoint_only_missing_email() -> None:
    """endpoint present but email empty -> INCOMPLETE_NO_LIMS (email is required)."""
    from exlab_wizard.config.models import NasConfig

    config = Config(
        paths=PathsConfig(app_root="/srv/exlab"),
        lims=LIMSConfig(endpoint="https://lims.example/api/v1", email=""),
        equipment=[_make_equipment()],
        orchestrator=_make_orchestrator(),
        nas=NasConfig(remote="nas01", base_root="/srv/nas"),
    )
    assert evaluate_setup_state(config) is SetupState.INCOMPLETE_NO_LIMS


# ---------------------------------------------------------------------------
# setup_state_next_action
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (SetupState.INCOMPLETE_NO_CONFIG, "set_paths"),
        (SetupState.INCOMPLETE_PATHS_UNWRITABLE, "set_paths"),
        (SetupState.INCOMPLETE_NO_EQUIPMENT, "add_equipment"),
        (SetupState.INCOMPLETE_NO_LIMS, "configure_lims"),
        (SetupState.INCOMPLETE_LIMS_UNREACHABLE, "test_lims"),
        (SetupState.READY, None),
    ],
)
def test_setup_state_next_action_table(state: SetupState, expected: str | None) -> None:
    assert setup_state_next_action(state) == expected


# ---------------------------------------------------------------------------
# setup_state_missing
# ---------------------------------------------------------------------------


def test_setup_state_missing_when_no_config() -> None:
    result = setup_state_missing(SetupState.INCOMPLETE_NO_CONFIG, None)
    assert result
    # Must contain the field/reason envelope shape.
    for entry in result:
        assert set(entry.keys()) >= {"field", "reason"}


def test_setup_state_missing_when_ready() -> None:
    assert setup_state_missing(SetupState.READY, _ready_config()) == []


def test_setup_state_missing_when_lims_unreachable_returns_empty() -> None:
    """Soft block: surfaces a banner, not a missing-field list."""
    assert setup_state_missing(SetupState.INCOMPLETE_LIMS_UNREACHABLE, _ready_config()) == []


def test_setup_state_missing_for_paths_reports_app_root_unwritable() -> None:
    """The paths gate now reports a single unwritable ``paths.app_root`` row."""
    config = Config(paths=PathsConfig(app_root="/srv/exlab"))
    result = setup_state_missing(SetupState.INCOMPLETE_PATHS_UNWRITABLE, config)
    assert result == [{"field": "paths.app_root", "reason": "unwritable"}]


def test_setup_state_missing_for_no_equipment() -> None:
    result = setup_state_missing(SetupState.INCOMPLETE_NO_EQUIPMENT, _ready_config())
    assert any(entry["field"] == "equipment" for entry in result)


def test_setup_state_missing_for_no_lims_lists_endpoint_email() -> None:
    config = Config(
        paths=PathsConfig(app_root="/srv/exlab"),
        lims=LIMSConfig(endpoint="", email=""),
        equipment=[_make_equipment()],
        orchestrator=_make_orchestrator(),
    )
    result = setup_state_missing(SetupState.INCOMPLETE_NO_LIMS, config)
    fields = {entry["field"] for entry in result}
    assert "lims.endpoint" in fields
    assert "lims.email" in fields


def test_setup_state_missing_for_unwritable_paths_with_none_config() -> None:
    """The paths-unwritable row is config-independent (single app_root row)."""
    result = setup_state_missing(SetupState.INCOMPLETE_PATHS_UNWRITABLE, None)
    assert result == [{"field": "paths.app_root", "reason": "unwritable"}]


def test_setup_state_missing_for_no_lims_with_none_config() -> None:
    """When config is None but state is INCOMPLETE_NO_LIMS, the whole lims
    block is reported as unset (we can't introspect individual subfields
    without a config)."""
    result = setup_state_missing(SetupState.INCOMPLETE_NO_LIMS, None)
    assert result == [{"field": "lims", "reason": "unset"}]


def test_setup_state_missing_for_no_lims_flags_keyring_when_endpoint_email_set() -> None:
    """endpoint+email both filled in but no offline catalogue -> the
    keyring-password slot is flagged as missing_in_keyring."""
    config = Config(
        paths=PathsConfig(app_root="/srv/exlab"),
        lims=LIMSConfig(
            endpoint="https://lims.example/api/v1",
            email="op@lab.example",
            offline_catalogue_path="",
        ),
        equipment=[_make_equipment()],
        orchestrator=_make_orchestrator(),
    )
    result = setup_state_missing(SetupState.INCOMPLETE_NO_LIMS, config)
    fields = {(entry["field"], entry["reason"]) for entry in result}
    assert ("lims.password", "missing_in_keyring") in fields
    # The endpoint/email pair is fully populated so neither is flagged.
    flagged_field_names = {entry["field"] for entry in result}
    assert "lims.endpoint" not in flagged_field_names
    assert "lims.email" not in flagged_field_names


def test_setup_state_missing_unrecognized_state_returns_empty() -> None:
    """Defensive fallback: any value that isn't a recognized SetupState
    returns an empty list rather than raising. Exercises the trailing
    ``return []`` after the if-chain."""

    class _BogusState:
        # Compares unequal to every SetupState, so neither ``in`` nor ``is``
        # checks in setup_state_missing match.
        def __eq__(self, other: object) -> bool:
            return False

        def __hash__(self) -> int:
            return 0

    result = setup_state_missing(_BogusState(), None)  # type: ignore[arg-type]
    assert result == []
