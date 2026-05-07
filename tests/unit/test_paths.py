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
from exlab_wizard.constants import RunKind, SetupState
from exlab_wizard.errors import ConfigError
from exlab_wizard.paths import (
    canonicalize_equipment_id,
    compose_project_path,
    compose_run_path,
    default_orchestrator_staging_root,
    ensure_central_log_dir,
    ensure_dir,
    ensure_state_dir,
    evaluate_setup_state,
    os_cache_path,
    os_central_log_path,
    os_config_path,
    os_state_path,
    setup_state_missing,
    setup_state_next_action,
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
            "local_root": "/data/lab",
            "nas_root": "//nas01/lab",
            "completeness_signal": "sentinel_file",
            "sentinel_filename": "done.flag",
            "transport": {
                "type": "rclone",
                "rclone_remote": "lab-nas",
                "rclone_remote_path": "lab/CONFOCAL_01",
            },
        }
    )


def _ready_config() -> Config:
    """Construct a fully READY Config (paths + equipment + LIMS endpoint+email)."""
    return Config(
        paths=PathsConfig(
            templates_dir="/srv/templates",
            plugin_dir="/srv/plugins",
            local_root="/data/lab",
        ),
        lims=LIMSConfig(endpoint="https://lims.example/api/v1", email="op@lab.example"),
        equipment=[_make_equipment()],
    )


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Return a fake HOME under tmp_path; route ``Path.home()`` to it."""
    home = tmp_path / "home" / "operator"
    home.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    # Strip any inherited XDG / APPDATA so each test asserts its own state.
    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "APPDATA", "LOCALAPPDATA"):
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
# default_orchestrator_staging_root
# ---------------------------------------------------------------------------


def test_default_orchestrator_staging_root_posix(
    monkeypatch: pytest.MonkeyPatch, fake_home: Path
) -> None:
    monkeypatch.setattr("sys.platform", "linux")
    assert default_orchestrator_staging_root() == Path("/staging")


def test_default_orchestrator_staging_root_macos(
    monkeypatch: pytest.MonkeyPatch, fake_home: Path
) -> None:
    monkeypatch.setattr("sys.platform", "darwin")
    assert default_orchestrator_staging_root() == Path("/staging")


def test_default_orchestrator_staging_root_windows(
    monkeypatch: pytest.MonkeyPatch, fake_home: Path
) -> None:
    monkeypatch.setattr("sys.platform", "win32")
    local = fake_home / "AppData" / "Local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    expected = local / "exlab-wizard" / "staging"
    assert default_orchestrator_staging_root() == expected


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
_EXPECTED_STAMP = "2026-04-17T14-32-00"


def test_compose_run_path_experimental(tmp_path: Path) -> None:
    result = compose_run_path(
        local_root=tmp_path,
        equipment_id="CONFOCAL_01",
        project_short_id="PROJ-0042",
        run_kind=RunKind.EXPERIMENTAL,
        run_date=_RUN_DATE,
    )
    expected = tmp_path / "CONFOCAL_01" / "PROJ-0042" / f"Run_{_EXPECTED_STAMP}"
    assert result == expected


def test_compose_run_path_test(tmp_path: Path) -> None:
    result = compose_run_path(
        local_root=tmp_path,
        equipment_id="CONFOCAL_01",
        project_short_id="PROJ-0042",
        run_kind=RunKind.TEST,
        run_date=_RUN_DATE,
    )
    expected = tmp_path / "CONFOCAL_01" / "PROJ-0042" / "TestRuns" / f"TestRun_{_EXPECTED_STAMP}"
    assert result == expected


def test_compose_run_path_strftime_format() -> None:
    """The leaf is ISO 8601 with colons replaced by hyphens. §3.1."""
    when = datetime(2030, 12, 31, 23, 59, 59)
    result = compose_run_path(
        local_root=Path("/data/lab"),
        equipment_id="FLOW_01",
        project_short_id="PROJ-1234",
        run_kind=RunKind.EXPERIMENTAL,
        run_date=when,
    )
    assert result.name == "Run_2030-12-31T23-59-59"
    assert ":" not in result.name


def test_compose_run_path_rejects_invalid_equipment_id(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        compose_run_path(
            local_root=tmp_path,
            equipment_id="confocal_01",
            project_short_id="PROJ-0042",
            run_kind=RunKind.EXPERIMENTAL,
            run_date=_RUN_DATE,
        )


def test_compose_run_path_rejects_invalid_project_short_id(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        compose_run_path(
            local_root=tmp_path,
            equipment_id="CONFOCAL_01",
            project_short_id="not-a-proj-id",
            run_kind=RunKind.EXPERIMENTAL,
            run_date=_RUN_DATE,
        )


def test_compose_project_path_basic(tmp_path: Path) -> None:
    result = compose_project_path(
        local_root=tmp_path,
        equipment_id="CONFOCAL_01",
        project_short_id="PROJ-0042",
    )
    expected = tmp_path / "CONFOCAL_01" / "PROJ-0042"
    assert result == expected


def test_compose_project_path_rejects_invalid_equipment_id(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        compose_project_path(
            local_root=tmp_path,
            equipment_id="bad id",
            project_short_id="PROJ-0042",
        )


def test_compose_project_path_rejects_invalid_short_id(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        compose_project_path(
            local_root=tmp_path,
            equipment_id="CONFOCAL_01",
            project_short_id="proj-0042",
        )


def test_compose_run_path_rejects_non_str_project_short_id(tmp_path: Path) -> None:
    """Non-str project_short_id hits the isinstance guard, not the regex."""
    with pytest.raises(ConfigError) as info:
        compose_run_path(
            local_root=tmp_path,
            equipment_id="CONFOCAL_01",
            project_short_id=None,  # type: ignore[arg-type]
            run_kind=RunKind.EXPERIMENTAL,
            run_date=_RUN_DATE,
        )
    assert "non-empty string" in str(info.value)


def test_compose_run_path_rejects_empty_project_short_id(tmp_path: Path) -> None:
    """Empty string project_short_id also hits the same guard."""
    with pytest.raises(ConfigError) as info:
        compose_run_path(
            local_root=tmp_path,
            equipment_id="CONFOCAL_01",
            project_short_id="",
            run_kind=RunKind.EXPERIMENTAL,
            run_date=_RUN_DATE,
        )
    assert "non-empty string" in str(info.value)


def test_compose_project_path_rejects_non_str_short_id(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as info:
        compose_project_path(
            local_root=tmp_path,
            equipment_id="CONFOCAL_01",
            project_short_id=42,  # type: ignore[arg-type]
        )
    assert "non-empty string" in str(info.value)


# ---------------------------------------------------------------------------
# evaluate_setup_state
# ---------------------------------------------------------------------------


def test_evaluate_setup_state_no_config() -> None:
    assert evaluate_setup_state(None) is SetupState.INCOMPLETE_NO_CONFIG


def test_evaluate_setup_state_missing_paths() -> None:
    config = Config(
        paths=PathsConfig(templates_dir="", plugin_dir="", local_root=""),
        equipment=[_make_equipment()],
    )
    assert evaluate_setup_state(config) is SetupState.INCOMPLETE_MISSING_PATHS


def test_evaluate_setup_state_missing_paths_partial() -> None:
    """Only one path is empty -- still INCOMPLETE_MISSING_PATHS."""
    config = Config(
        paths=PathsConfig(
            templates_dir="/srv/templates",
            plugin_dir="/srv/plugins",
            local_root="",
        ),
        equipment=[_make_equipment()],
    )
    assert evaluate_setup_state(config) is SetupState.INCOMPLETE_MISSING_PATHS


def test_evaluate_setup_state_no_equipment() -> None:
    config = Config(
        paths=PathsConfig(
            templates_dir="/srv/templates",
            plugin_dir="/srv/plugins",
            local_root="/data/lab",
        ),
        equipment=[],
    )
    assert evaluate_setup_state(config) is SetupState.INCOMPLETE_NO_EQUIPMENT


def test_evaluate_setup_state_no_lims() -> None:
    config = Config(
        paths=PathsConfig(
            templates_dir="/srv/templates",
            plugin_dir="/srv/plugins",
            local_root="/data/lab",
        ),
        lims=LIMSConfig(endpoint="", email="", offline_catalogue_path=""),
        equipment=[_make_equipment()],
    )
    assert evaluate_setup_state(config) is SetupState.INCOMPLETE_NO_LIMS


def test_evaluate_setup_state_lims_via_offline_catalogue() -> None:
    """Offline catalogue path satisfies the LIMS slot without endpoint+email."""
    config = Config(
        paths=PathsConfig(
            templates_dir="/srv/templates",
            plugin_dir="/srv/plugins",
            local_root="/data/lab",
        ),
        lims=LIMSConfig(
            endpoint="",
            email="",
            offline_catalogue_path="/mnt/share/offline_catalogue.json",
        ),
        equipment=[_make_equipment()],
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


def test_evaluate_setup_state_endpoint_only_missing_email() -> None:
    """endpoint present but email empty -> INCOMPLETE_NO_LIMS (email is required)."""
    config = Config(
        paths=PathsConfig(
            templates_dir="/srv/templates",
            plugin_dir="/srv/plugins",
            local_root="/data/lab",
        ),
        lims=LIMSConfig(endpoint="https://lims.example/api/v1", email=""),
        equipment=[_make_equipment()],
    )
    assert evaluate_setup_state(config) is SetupState.INCOMPLETE_NO_LIMS


# ---------------------------------------------------------------------------
# setup_state_next_action
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (SetupState.INCOMPLETE_NO_CONFIG, "set_paths"),
        (SetupState.INCOMPLETE_MISSING_PATHS, "set_paths"),
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


def test_setup_state_missing_for_paths_lists_each_unset() -> None:
    config = Config(
        paths=PathsConfig(templates_dir="", plugin_dir="/srv/plugins", local_root=""),
    )
    result = setup_state_missing(SetupState.INCOMPLETE_MISSING_PATHS, config)
    fields = {entry["field"] for entry in result}
    assert "paths.templates_dir" in fields
    assert "paths.local_root" in fields
    assert "paths.plugin_dir" not in fields


def test_setup_state_missing_for_no_equipment() -> None:
    result = setup_state_missing(SetupState.INCOMPLETE_NO_EQUIPMENT, _ready_config())
    assert any(entry["field"] == "equipment" for entry in result)


def test_setup_state_missing_for_no_lims_lists_endpoint_email() -> None:
    config = Config(
        paths=PathsConfig(
            templates_dir="/srv/templates",
            plugin_dir="/srv/plugins",
            local_root="/data/lab",
        ),
        lims=LIMSConfig(endpoint="", email=""),
        equipment=[_make_equipment()],
    )
    result = setup_state_missing(SetupState.INCOMPLETE_NO_LIMS, config)
    fields = {entry["field"] for entry in result}
    assert "lims.endpoint" in fields
    assert "lims.email" in fields


def test_setup_state_missing_for_missing_paths_with_none_config() -> None:
    """When config is None but state is INCOMPLETE_MISSING_PATHS, every paths
    field is reported as unset. This is a defensive branch for callers that
    pass the state without the config object."""
    result = setup_state_missing(SetupState.INCOMPLETE_MISSING_PATHS, None)
    fields = {entry["field"] for entry in result}
    assert fields == {"paths.templates_dir", "paths.plugin_dir", "paths.local_root"}
    for entry in result:
        assert entry["reason"] == "unset"


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
        paths=PathsConfig(
            templates_dir="/srv/templates",
            plugin_dir="/srv/plugins",
            local_root="/data/lab",
        ),
        lims=LIMSConfig(
            endpoint="https://lims.example/api/v1",
            email="op@lab.example",
            offline_catalogue_path="",
        ),
        equipment=[_make_equipment()],
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
