"""Unit tests for ``exlab_wizard.config.models``.

Pydantic models that mirror ``config.yaml`` (Backend Spec §9). These tests
exercise the happy path, every ``Field(...)`` constraint, every
``field_validator``, and every ``model_validator`` -- including the
cross-field invariants on ``Config``. The loader (``exlab_wizard.config.loader``)
is responsible for converting Pydantic ``ValidationError``s into ``ConfigError``;
this file only checks the model layer in isolation, so most assertions raise
``ValidationError`` directly. The one exception is :class:`RsyncSshTransport`,
which raises a custom ``ConfigError`` from a ``mode='before'`` validator before
Pydantic ever sees the input.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from exlab_wizard.config.models import (
    BandwidthConfig,
    BandwidthWindow,
    Config,
    EquipmentConfig,
    LIMSConfig,
    LoggingConfig,
    NASCleanupConfig,
    OperatorsConfig,
    OrchestratorConfig,
    OrchestratorStagingCleanup,
    OrchestratorStagingTransport,
    PathsConfig,
    PluginsConfig,
    RcloneTransport,
    READMEConfig,
    READMEDefaultField,
    RsyncSshTransport,
    SyncConfig,
    ValidatorConfig,
)
from exlab_wizard.errors import ConfigError

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _rclone_transport_dict() -> dict:
    """Minimal valid rclone transport block."""
    return {
        "type": "rclone",
        "rclone_remote": "lab-nas",
        "rclone_remote_path": "lab/CONFOCAL_01",
    }


def _rsync_transport_dict() -> dict:
    """Minimal valid rsync_ssh transport block."""
    return {
        "type": "rsync_ssh",
        "ssh_target": "labuser@nas01.lab.example",
        "remote_path": "/srv/lab/FLOW_01",
    }


def _equipment_dict(
    *,
    equipment_id: str = "CONFOCAL_01",
    completeness_signal: str = "sentinel_file",
    sentinel_filename: str | None = "acquisition_complete.flag",
    manifest_filename: str | None = None,
    transport: dict | None = None,
) -> dict:
    """Build a valid EquipmentConfig dict with sensible defaults."""
    return {
        "id": equipment_id,
        "label": "Confocal Microscope 1",
        "local_root": "/data/lab",
        "nas_root": "//nas01/lab",
        "completeness_signal": completeness_signal,
        "sentinel_filename": sentinel_filename,
        "manifest_filename": manifest_filename,
        "transport": transport or _rclone_transport_dict(),
    }


def _full_config_dict() -> dict:
    """Full Config dict mirroring the §9 example. Used by round-trip tests."""
    return {
        "paths": {
            "templates_dir": "/opt/templates",
            "plugin_dir": "/opt/plugins",
            "local_root": "/data/lab",
        },
        "lims": {
            "endpoint": "https://lims.lab.example/api/v1",
            "email": "alex.nguyen@lab.example",
            "cache_ttl_hours": 24,
            "offline_catalogue_path": "",
        },
        "readme": {
            "defaults": [
                {
                    "id": "irb_protocol",
                    "label": "IRB Protocol Number",
                    "type": "string",
                    "required": False,
                    "default": "",
                },
            ],
        },
        "equipment": [
            {
                "id": "CONFOCAL_01",
                "label": "Confocal Microscope 1",
                "local_root": "/data/lab",
                "nas_root": "//nas01/lab",
                "completeness_signal": "sentinel_file",
                "sentinel_filename": "acquisition_complete.flag",
                "manifest_filename": None,
                "transport": {
                    "type": "rclone",
                    "rclone_remote": "lab-nas",
                    "rclone_remote_path": "lab/CONFOCAL_01",
                    "bandwidth": {
                        "upload_mbps": 50.0,
                        "schedule": [
                            {
                                "days": ["mon", "tue", "wed", "thu", "fri"],
                                "from": "08:00",
                                "to": "18:00",
                            },
                        ],
                    },
                },
                "orchestrator_staging_transport": None,
            },
            {
                "id": "FLOW_01",
                "label": "Flow Cytometer 1",
                "local_root": "/data/lab",
                "nas_root": "/mnt/nas/lab",
                "completeness_signal": "manifest",
                "sentinel_filename": None,
                "manifest_filename": "run_manifest.json",
                "transport": {
                    "type": "rsync_ssh",
                    "ssh_target": "labuser@nas01.lab.example",
                    "ssh_key_path": "~/.ssh/id_ed25519",
                    "remote_path": "/srv/lab/FLOW_01",
                    "bandwidth": {
                        "upload_mbps": None,
                        "schedule": [],
                    },
                },
                "orchestrator_staging_transport": None,
            },
        ],
        "nas_cleanup": {
            "enabled": True,
            "min_verify_passes": 2,
            "min_age_hours": 24,
            "retain_cache": True,
        },
        "logging": {
            "level": "INFO",
            "central_log_max_mb": 10,
            "central_log_keep": 5,
        },
        "operators": {"allowlist": []},
        "validator": {
            "content_scan_max_mib": 5,
            "content_scan_extensions": [
                ".txt",
                ".md",
                ".csv",
                ".tsv",
                ".json",
                ".yaml",
                ".yml",
                ".toml",
                ".ini",
                ".cfg",
                ".conf",
                ".xml",
                ".sh",
                ".py",
            ],
        },
        "plugins": {"allow_network": False},
        "sync": {"enabled": True, "retry_attempts": 3},
        "orchestrator": {
            "enabled": False,
            "label": "",
            "staging_root": "",
            "staging_cleanup": {"mode": "manual", "retain_hours": 24},
        },
    }


# ---------------------------------------------------------------------------
# PathsConfig
# ---------------------------------------------------------------------------


def test_paths_config_defaults_are_empty_strings() -> None:
    paths = PathsConfig()
    assert paths.templates_dir == ""
    assert paths.plugin_dir == ""
    assert paths.local_root == ""


def test_paths_config_rejects_unknown_keys() -> None:
    with pytest.raises(ValidationError):
        PathsConfig(unknown_key="x")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# LIMSConfig
# ---------------------------------------------------------------------------


def test_lims_config_defaults() -> None:
    lims = LIMSConfig()
    assert lims.endpoint == ""
    assert lims.email == ""
    assert lims.cache_ttl_hours == 24
    assert lims.offline_catalogue_path == ""


def test_lims_config_rejects_negative_cache_ttl_hours() -> None:
    with pytest.raises(ValidationError):
        LIMSConfig(cache_ttl_hours=-1)


def test_lims_config_zero_cache_ttl_is_allowed() -> None:
    # ge=0 in the model: zero TTL is legal (no caching).
    lims = LIMSConfig(cache_ttl_hours=0)
    assert lims.cache_ttl_hours == 0


# ---------------------------------------------------------------------------
# READMEDefaultField
# ---------------------------------------------------------------------------


def test_readme_default_field_minimal_string_field() -> None:
    field = READMEDefaultField(id="irb_protocol", label="IRB Protocol", type="string")
    assert field.required is False
    assert field.default == ""
    assert field.options is None


def test_readme_default_field_id_must_match_template_question_id_pattern() -> None:
    with pytest.raises(ValidationError):
        READMEDefaultField(id="IRB_Protocol", label="IRB Protocol", type="string")


def test_readme_default_field_id_rejects_leading_digit() -> None:
    with pytest.raises(ValidationError):
        READMEDefaultField(id="1foo", label="Foo", type="string")


def test_readme_default_field_label_must_be_non_empty() -> None:
    with pytest.raises(ValidationError):
        READMEDefaultField(id="x", label="", type="string")


def test_readme_default_field_rejects_unknown_type() -> None:
    with pytest.raises(ValidationError):
        READMEDefaultField(id="x", label="X", type="bogus")  # type: ignore[arg-type]


def test_readme_choice_field_requires_options() -> None:
    with pytest.raises(ValidationError) as info:
        READMEDefaultField(id="color", label="Color", type="choice")
    assert "options" in str(info.value).lower()


def test_readme_choice_field_rejects_empty_options_list() -> None:
    with pytest.raises(ValidationError):
        READMEDefaultField(id="color", label="Color", type="choice", options=[])


def test_readme_choice_field_accepts_non_empty_options() -> None:
    field = READMEDefaultField(id="color", label="Color", type="choice", options=["red", "blue"])
    assert field.options == ["red", "blue"]


def test_readme_non_choice_field_does_not_require_options() -> None:
    # The validator only triggers on type=='choice'.
    READMEDefaultField(id="when", label="When", type="date")
    READMEDefaultField(id="agreed", label="Agreed", type="boolean")
    READMEDefaultField(id="notes", label="Notes", type="text")


# ---------------------------------------------------------------------------
# READMEConfig
# ---------------------------------------------------------------------------


def test_readme_config_default_is_empty_list() -> None:
    cfg = READMEConfig()
    assert cfg.defaults == []


# ---------------------------------------------------------------------------
# BandwidthWindow
# ---------------------------------------------------------------------------


def test_bandwidth_window_happy_path() -> None:
    win = BandwidthWindow.model_validate({"days": ["mon", "tue"], "from": "08:00", "to": "18:00"})
    assert win.days == ["mon", "tue"]
    assert win.from_ == "08:00"
    assert win.to == "18:00"


def test_bandwidth_window_rejects_empty_days_list() -> None:
    with pytest.raises(ValidationError):
        BandwidthWindow.model_validate({"days": [], "from": "08:00", "to": "18:00"})


def test_bandwidth_window_rejects_invalid_day() -> None:
    with pytest.raises(ValidationError):
        BandwidthWindow.model_validate({"days": ["funday"], "from": "08:00", "to": "18:00"})


def test_bandwidth_window_from_must_be_before_to() -> None:
    with pytest.raises(ValidationError):
        BandwidthWindow.model_validate({"days": ["mon"], "from": "18:00", "to": "08:00"})


def test_bandwidth_window_equal_from_and_to_is_invalid() -> None:
    # Strict less-than: equal endpoints are rejected.
    with pytest.raises(ValidationError):
        BandwidthWindow.model_validate({"days": ["mon"], "from": "08:00", "to": "08:00"})


def test_bandwidth_window_rejects_non_hhmm_time() -> None:
    with pytest.raises(ValidationError):
        BandwidthWindow.model_validate({"days": ["mon"], "from": "8:00", "to": "18:00"})


def test_bandwidth_window_rejects_invalid_hour() -> None:
    with pytest.raises(ValidationError):
        BandwidthWindow.model_validate({"days": ["mon"], "from": "24:00", "to": "23:59"})


def test_bandwidth_window_rejects_invalid_minute() -> None:
    with pytest.raises(ValidationError):
        BandwidthWindow.model_validate({"days": ["mon"], "from": "08:60", "to": "18:00"})


# ---------------------------------------------------------------------------
# BandwidthConfig
# ---------------------------------------------------------------------------


def test_bandwidth_config_defaults() -> None:
    bw = BandwidthConfig()
    assert bw.upload_mbps is None
    assert bw.schedule == []


def test_bandwidth_config_rejects_zero_upload_mbps() -> None:
    with pytest.raises(ValidationError):
        BandwidthConfig(upload_mbps=0)


def test_bandwidth_config_rejects_negative_upload_mbps() -> None:
    with pytest.raises(ValidationError):
        BandwidthConfig(upload_mbps=-1.0)


def test_bandwidth_config_accepts_positive_upload_mbps() -> None:
    bw = BandwidthConfig(upload_mbps=50.0)
    assert bw.upload_mbps == 50.0


# ---------------------------------------------------------------------------
# RcloneTransport
# ---------------------------------------------------------------------------


def test_rclone_transport_minimal() -> None:
    t = RcloneTransport.model_validate(_rclone_transport_dict())
    assert t.type == "rclone"
    assert t.rclone_remote == "lab-nas"


def test_rclone_transport_rejects_missing_remote() -> None:
    with pytest.raises(ValidationError):
        RcloneTransport.model_validate({"type": "rclone", "rclone_remote_path": "x"})


def test_rclone_transport_rejects_empty_remote() -> None:
    with pytest.raises(ValidationError):
        RcloneTransport.model_validate(
            {"type": "rclone", "rclone_remote": "", "rclone_remote_path": "x"}
        )


def test_rclone_transport_rejects_wrong_type_tag() -> None:
    with pytest.raises(ValidationError):
        RcloneTransport.model_validate(
            {"type": "rsync_ssh", "rclone_remote": "x", "rclone_remote_path": "y"}
        )


# ---------------------------------------------------------------------------
# RsyncSshTransport
# ---------------------------------------------------------------------------


def test_rsync_ssh_transport_minimal() -> None:
    t = RsyncSshTransport.model_validate(_rsync_transport_dict())
    assert t.type == "rsync_ssh"
    assert t.ssh_key_path == "~/.ssh/id_ed25519"


def test_rsync_ssh_rejects_password_field() -> None:
    # The mode='before' validator catches this and raises ConfigError directly,
    # not a Pydantic ValidationError, so the loader's user-facing message is
    # crisp even when extra='forbid' would otherwise fire first.
    bad = dict(_rsync_transport_dict())
    bad["password"] = "hunter2"
    with pytest.raises(ConfigError) as info:
        RsyncSshTransport.model_validate(bad)
    assert "password" in str(info.value).lower()


def test_rsync_ssh_rejects_empty_ssh_target() -> None:
    bad = dict(_rsync_transport_dict())
    bad["ssh_target"] = ""
    with pytest.raises(ValidationError):
        RsyncSshTransport.model_validate(bad)


def test_rsync_ssh_rejects_empty_remote_path() -> None:
    bad = dict(_rsync_transport_dict())
    bad["remote_path"] = ""
    with pytest.raises(ValidationError):
        RsyncSshTransport.model_validate(bad)


# ---------------------------------------------------------------------------
# Discriminated EquipmentTransport union
# ---------------------------------------------------------------------------


def test_equipment_transport_discriminates_on_type_rclone() -> None:
    eq = EquipmentConfig.model_validate(_equipment_dict())
    assert isinstance(eq.transport, RcloneTransport)


def test_equipment_transport_discriminates_on_type_rsync() -> None:
    eq = EquipmentConfig.model_validate(
        _equipment_dict(
            equipment_id="FLOW_01",
            completeness_signal="manifest",
            sentinel_filename=None,
            manifest_filename="run_manifest.json",
            transport=_rsync_transport_dict(),
        )
    )
    assert isinstance(eq.transport, RsyncSshTransport)


def test_equipment_transport_rejects_unknown_type() -> None:
    bad = _equipment_dict()
    bad["transport"] = {"type": "ftp", "host": "ftp.example.com"}
    with pytest.raises(ValidationError):
        EquipmentConfig.model_validate(bad)


# ---------------------------------------------------------------------------
# OrchestratorStagingTransport
# ---------------------------------------------------------------------------


def test_orchestrator_staging_transport_smb_mount() -> None:
    t = OrchestratorStagingTransport(
        type="smb_mount", mount_point="/mnt/staging", staging_subpath="CONFOCAL_01"
    )
    assert t.type == "smb_mount"


def test_orchestrator_staging_transport_file_transfer() -> None:
    t = OrchestratorStagingTransport(
        type="file_transfer", mount_point="/staging", staging_subpath="FLOW_01"
    )
    assert t.type == "file_transfer"


def test_orchestrator_staging_transport_rejects_unknown_type() -> None:
    with pytest.raises(ValidationError):
        OrchestratorStagingTransport(
            type="rclone",  # type: ignore[arg-type]
            mount_point="/mnt",
            staging_subpath="X",
        )


# ---------------------------------------------------------------------------
# EquipmentConfig
# ---------------------------------------------------------------------------


def test_equipment_id_regex_accepts_canonical_form() -> None:
    eq = EquipmentConfig.model_validate(_equipment_dict(equipment_id="CONFOCAL_01"))
    assert eq.id == "CONFOCAL_01"


def test_equipment_id_rejects_lowercase() -> None:
    with pytest.raises(ValidationError):
        EquipmentConfig.model_validate(_equipment_dict(equipment_id="confocal_01"))


def test_equipment_id_rejects_hyphen() -> None:
    with pytest.raises(ValidationError):
        EquipmentConfig.model_validate(_equipment_dict(equipment_id="CONFOCAL-01"))


def test_equipment_id_rejects_leading_digit() -> None:
    with pytest.raises(ValidationError):
        EquipmentConfig.model_validate(_equipment_dict(equipment_id="1CONFOCAL"))


def test_equipment_id_rejects_too_long() -> None:
    too_long = "X" * 33
    with pytest.raises(ValidationError):
        EquipmentConfig.model_validate(_equipment_dict(equipment_id=too_long))


def test_equipment_id_accepts_max_length() -> None:
    at_max = "X" * 32
    eq = EquipmentConfig.model_validate(_equipment_dict(equipment_id=at_max))
    assert eq.id == at_max


def test_completeness_signal_sentinel_requires_filename() -> None:
    with pytest.raises(ValidationError) as info:
        EquipmentConfig.model_validate(
            _equipment_dict(completeness_signal="sentinel_file", sentinel_filename=None)
        )
    assert "sentinel_filename" in str(info.value)


def test_completeness_signal_manifest_requires_filename() -> None:
    with pytest.raises(ValidationError) as info:
        EquipmentConfig.model_validate(
            _equipment_dict(
                completeness_signal="manifest",
                sentinel_filename=None,
                manifest_filename=None,
            )
        )
    assert "manifest_filename" in str(info.value)


def test_equipment_label_must_be_non_empty() -> None:
    bad = _equipment_dict()
    bad["label"] = ""
    with pytest.raises(ValidationError):
        EquipmentConfig.model_validate(bad)


def test_equipment_local_root_must_be_non_empty() -> None:
    bad = _equipment_dict()
    bad["local_root"] = ""
    with pytest.raises(ValidationError):
        EquipmentConfig.model_validate(bad)


def test_equipment_nas_root_must_be_non_empty() -> None:
    bad = _equipment_dict()
    bad["nas_root"] = ""
    with pytest.raises(ValidationError):
        EquipmentConfig.model_validate(bad)


def test_equipment_orchestrator_staging_transport_optional() -> None:
    eq = EquipmentConfig.model_validate(_equipment_dict())
    assert eq.orchestrator_staging_transport is None


# ---------------------------------------------------------------------------
# NASCleanupConfig
# ---------------------------------------------------------------------------


def test_nas_cleanup_defaults() -> None:
    nc = NASCleanupConfig()
    assert nc.enabled is True
    assert nc.min_verify_passes == 2
    assert nc.min_age_hours == 24
    assert nc.retain_cache is True


def test_nas_cleanup_min_verify_passes_at_least_one() -> None:
    with pytest.raises(ValidationError):
        NASCleanupConfig(min_verify_passes=0)


def test_nas_cleanup_min_age_hours_non_negative() -> None:
    with pytest.raises(ValidationError):
        NASCleanupConfig(min_age_hours=-1)


# ---------------------------------------------------------------------------
# LoggingConfig
# ---------------------------------------------------------------------------


def test_logging_level_normalized_to_uppercase() -> None:
    cfg = LoggingConfig(level="info")
    assert cfg.level == "INFO"


def test_logging_level_rejects_unknown() -> None:
    with pytest.raises(ValidationError):
        LoggingConfig(level="TRACE")


def test_logging_level_accepts_all_canonical_values() -> None:
    for level in ("DEBUG", "INFO", "WARN", "ERROR"):
        cfg = LoggingConfig(level=level)
        assert cfg.level == level


def test_logging_level_strips_whitespace_and_normalizes() -> None:
    cfg = LoggingConfig(level="  Info  ")
    assert cfg.level == "INFO"


def test_logging_level_rejects_non_string() -> None:
    # The mode='before' validator rejects non-string inputs explicitly so
    # the operator gets a typed error message rather than the generic
    # "value is not a valid string" Pydantic emits for str fields.
    with pytest.raises(ValidationError) as info:
        Config.model_validate({"logging": {"level": 42}})
    assert "logging.level must be a string" in str(info.value)
    assert "int" in str(info.value)


def test_logging_level_rejects_non_string_directly() -> None:
    # Same branch reached via the LoggingConfig model directly so the test
    # is independent of the Config wrapper.
    with pytest.raises(ValidationError) as info:
        LoggingConfig(level=42)  # type: ignore[arg-type]
    assert "must be a string" in str(info.value)


def test_logging_central_log_max_mb_at_least_one() -> None:
    with pytest.raises(ValidationError):
        LoggingConfig(central_log_max_mb=0)


def test_logging_central_log_keep_at_least_one() -> None:
    with pytest.raises(ValidationError):
        LoggingConfig(central_log_keep=0)


# ---------------------------------------------------------------------------
# OperatorsConfig
# ---------------------------------------------------------------------------


def test_operators_config_defaults_to_empty_allowlist() -> None:
    cfg = OperatorsConfig()
    assert cfg.allowlist == []


def test_operators_config_accepts_arbitrary_strings() -> None:
    cfg = OperatorsConfig(allowlist=["asmith", "jlee", "alex.nguyen"])
    assert cfg.allowlist == ["asmith", "jlee", "alex.nguyen"]


# ---------------------------------------------------------------------------
# ValidatorConfig
# ---------------------------------------------------------------------------


def test_validator_config_defaults() -> None:
    cfg = ValidatorConfig()
    assert cfg.content_scan_max_mib == 5
    assert cfg.content_scan_extensions[0] == ".txt"
    assert ".py" in cfg.content_scan_extensions


def test_validator_content_scan_max_mib_at_least_one() -> None:
    with pytest.raises(ValidationError):
        ValidatorConfig(content_scan_max_mib=0)


def test_validator_extensions_must_start_with_dot() -> None:
    with pytest.raises(ValidationError):
        ValidatorConfig(content_scan_extensions=["txt"])


def test_validator_extensions_rejects_dotless_among_others() -> None:
    with pytest.raises(ValidationError):
        ValidatorConfig(content_scan_extensions=[".csv", "tsv"])


# ---------------------------------------------------------------------------
# PluginsConfig
# ---------------------------------------------------------------------------


def test_plugins_config_default_allow_network_false() -> None:
    cfg = PluginsConfig()
    assert cfg.allow_network is False


# ---------------------------------------------------------------------------
# SyncConfig
# ---------------------------------------------------------------------------


def test_sync_config_defaults() -> None:
    cfg = SyncConfig()
    assert cfg.enabled is True
    assert cfg.retry_attempts == 3


def test_sync_config_retry_attempts_non_negative() -> None:
    with pytest.raises(ValidationError):
        SyncConfig(retry_attempts=-1)


# ---------------------------------------------------------------------------
# OrchestratorStagingCleanup
# ---------------------------------------------------------------------------


def test_orchestrator_staging_cleanup_defaults() -> None:
    cfg = OrchestratorStagingCleanup()
    assert cfg.mode == "manual"
    assert cfg.retain_hours == 24


def test_orchestrator_staging_cleanup_scheduled_requires_positive_hours() -> None:
    # ge=1 already enforces this; the explicit test also pins the scheduled rule.
    with pytest.raises(ValidationError):
        OrchestratorStagingCleanup(mode="scheduled", retain_hours=0)


def test_orchestrator_staging_cleanup_rejects_unknown_mode() -> None:
    with pytest.raises(ValidationError):
        OrchestratorStagingCleanup(mode="weekly")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# OrchestratorConfig
# ---------------------------------------------------------------------------


def test_orchestrator_config_defaults() -> None:
    cfg = OrchestratorConfig()
    assert cfg.enabled is False
    assert cfg.label == ""
    assert cfg.staging_root == ""
    assert cfg.staging_cleanup.mode == "manual"


# ---------------------------------------------------------------------------
# Top-level Config: cross-field invariants
# ---------------------------------------------------------------------------


def test_config_fully_default_is_valid() -> None:
    cfg = Config()
    assert cfg.equipment == []
    assert cfg.orchestrator.enabled is False


def test_unique_equipment_ids_required() -> None:
    payload = {
        "equipment": [
            _equipment_dict(equipment_id="CONFOCAL_01"),
            _equipment_dict(equipment_id="CONFOCAL_01"),
        ],
    }
    with pytest.raises(ValidationError) as info:
        Config.model_validate(payload)
    assert "unique" in str(info.value).lower() or "duplicate" in str(info.value).lower()


def test_distinct_equipment_ids_accepted() -> None:
    payload = {
        "equipment": [
            _equipment_dict(equipment_id="CONFOCAL_01"),
            _equipment_dict(
                equipment_id="FLOW_01",
                completeness_signal="manifest",
                sentinel_filename=None,
                manifest_filename="run_manifest.json",
                transport=_rsync_transport_dict(),
            ),
        ],
    }
    cfg = Config.model_validate(payload)
    assert [e.id for e in cfg.equipment] == ["CONFOCAL_01", "FLOW_01"]


def test_orchestrator_enabled_requires_label() -> None:
    with pytest.raises(ValidationError):
        Config.model_validate(
            {
                "orchestrator": {
                    "enabled": True,
                    "label": "",
                    "staging_root": "/staging",
                }
            }
        )


def test_orchestrator_enabled_requires_staging_root() -> None:
    with pytest.raises(ValidationError):
        Config.model_validate(
            {
                "orchestrator": {
                    "enabled": True,
                    "label": "Lab Acquisition Station 01",
                    "staging_root": "",
                }
            }
        )


def test_orchestrator_enabled_with_both_fields_is_valid() -> None:
    cfg = Config.model_validate(
        {
            "orchestrator": {
                "enabled": True,
                "label": "Lab Acquisition Station 01",
                "staging_root": "/staging",
                "staging_cleanup": {"mode": "manual", "retain_hours": 24},
            }
        }
    )
    assert cfg.orchestrator.enabled is True
    assert cfg.orchestrator.label == "Lab Acquisition Station 01"


def test_config_rejects_unknown_top_level_key() -> None:
    with pytest.raises(ValidationError):
        Config.model_validate({"unknown_top_level_key": True})


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


def test_full_config_round_trip_via_dict() -> None:
    """Build a Config from a §9-shaped dict, dump it, and compare.

    ``model_dump()`` preserves field names (the BandwidthWindow ``from_`` field
    remains ``from`` because it was declared with ``alias='from'`` and
    ``populate_by_name=True``). We dump with ``by_alias=True`` so the round-trip
    matches the on-disk YAML form. Optional fields defaulting to ``None`` are
    emitted by Pydantic, so the source dict is normalized first.
    """
    source = _full_config_dict()
    # Optional README fields are emitted as None by Pydantic but are absent
    # from the §9 example. Add them so equality compares like-for-like.
    for entry in source["readme"]["defaults"]:
        entry.setdefault("options", None)
        entry.setdefault("hint", None)

    cfg = Config.model_validate(source)
    dumped = cfg.model_dump(mode="python", by_alias=True)
    assert dumped == source


def test_round_trip_preserves_bandwidth_alias_for_from() -> None:
    cfg = Config.model_validate(_full_config_dict())
    dumped = cfg.model_dump(mode="python", by_alias=True)
    schedule = dumped["equipment"][0]["transport"]["bandwidth"]["schedule"]
    assert schedule[0]["from"] == "08:00"
    assert schedule[0]["to"] == "18:00"
    assert "from_" not in schedule[0]
