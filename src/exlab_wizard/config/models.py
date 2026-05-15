"""Pydantic models that mirror ``config.yaml``. Backend Spec §9.

These models are the typed schema for the on-disk ``config.yaml``. The loader
(``exlab_wizard.config.loader``) parses YAML into a plain ``dict``, hands it to
``Config.model_validate``, and converts any Pydantic ``ValidationError`` into a
``ConfigError`` at the boundary; nothing here raises ``ConfigError`` directly
except for cases that need a custom message before the model layer sees the
input (for instance the ``password``-key rejection in
:class:`RsyncSshTransport`).

Style:
- ``model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)`` on
  every model so unknown keys raise a clear validation error.
- ``StrEnum`` values are accepted in either string or enum form; Pydantic v2
  lax mode coerces raw strings to enum members, and the spec stores the
  string value verbatim on dump (via ``StrEnum.value`` or explicit
  ``field_serializer``).
- All cross-field invariants from §9 are encoded as ``model_validator``s.
"""

from __future__ import annotations

from datetime import time
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from exlab_wizard.constants import (
    TEMPLATE_QUESTION_ID_PATTERN,
    BandwidthDay,
    CompletenessSignal,
    FieldType,
    OrchestratorTransportType,
    StagingCleanupMode,
    SyncMode,
)
from exlab_wizard.errors import ConfigError

__all__ = [
    "BandwidthConfig",
    "BandwidthWindow",
    "Config",
    "EquipmentConfig",
    "EquipmentTransport",
    "LIMSConfig",
    "LoggingConfig",
    "NASCleanupConfig",
    "OperatorsConfig",
    "OrchestratorConfig",
    "OrchestratorStagingCleanup",
    "OrchestratorStagingTransport",
    "PathsConfig",
    "PluginsConfig",
    "READMEConfig",
    "READMEDefaultField",
    "RcloneTransport",
    "RsyncSshTransport",
    "SyncConfig",
    "ValidatorConfig",
]


# Allowed log levels (case-insensitive on input, normalized to upper-case).
_ALLOWED_LOG_LEVELS: frozenset[str] = frozenset({"DEBUG", "INFO", "WARN", "ERROR"})


def _parse_hhmm(value: str, field_name: str) -> time:
    """Return ``datetime.time`` for a strict zero-padded ``HH:MM`` string.

    The wizard's bandwidth schedule is YAML-edited by humans, so we accept
    only the canonical 5-character ``HH:MM`` form (no seconds, no leading
    plus, no missing leading zeros). ``datetime.time.fromisoformat`` happens
    to accept ``HH:MM`` and ``HH:MM:SS`` and a few other variants, so we
    pre-check the length / colon position before delegating.
    """
    if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
        msg = f"{field_name} must be a zero-padded HH:MM string, got {value!r}"
        raise ValueError(msg)
    try:
        return time.fromisoformat(value)
    except ValueError as exc:
        msg = f"{field_name} must be a valid time in HH:MM, got {value!r}"
        raise ValueError(msg) from exc


# ---------------------------------------------------------------------------
# paths
# ---------------------------------------------------------------------------


class PathsConfig(BaseModel):
    """``paths:`` block. Templates / plugins / equipment-first local root."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    templates_dir: str = ""
    plugin_dir: str = ""
    local_root: str = ""


# ---------------------------------------------------------------------------
# lims
# ---------------------------------------------------------------------------


class LIMSConfig(BaseModel):
    """``lims:`` block. Read-only LIMS endpoint plus offline catalogue path."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    endpoint: str = ""
    email: str = ""
    cache_ttl_hours: int = Field(default=24, ge=0)
    offline_catalogue_path: str = ""


# ---------------------------------------------------------------------------
# readme
# ---------------------------------------------------------------------------


class READMEDefaultField(BaseModel):
    """One operator-defined extra README field. Backend Spec §9, §10."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str
    label: str = Field(min_length=1)
    type: FieldType
    required: bool = False
    default: Any = ""
    options: list[str] | None = None
    hint: str | None = None

    @field_validator("id")
    @classmethod
    def _id_matches_question_id_grammar(cls, value: str) -> str:
        if not TEMPLATE_QUESTION_ID_PATTERN.fullmatch(value):
            msg = (
                f"readme.defaults[].id {value!r} does not match "
                f"{TEMPLATE_QUESTION_ID_PATTERN.pattern}"
            )
            raise ValueError(msg)
        return value

    @field_serializer("type")
    def _serialize_type(self, value: FieldType) -> str:
        # Emit the bare string so YAML/JSON dumps round-trip the wire format.
        return value.value

    @model_validator(mode="after")
    def _choice_requires_non_empty_options(self) -> READMEDefaultField:
        match self.type:
            case FieldType.CHOICE:
                if not self.options:
                    msg = "readme.defaults[].options must be a non-empty list when type == 'choice'"
                    raise ValueError(msg)
            case _:
                pass
        return self


class READMEConfig(BaseModel):
    """``readme:`` block. Lab-policy fields layered on top of the core set."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    defaults: list[READMEDefaultField] = []


# ---------------------------------------------------------------------------
# bandwidth
# ---------------------------------------------------------------------------


class BandwidthWindow(BaseModel):
    """One ``{days, from, to}`` window. Backend Spec §9."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, populate_by_name=True)

    days: list[BandwidthDay] = Field(min_length=1)
    from_: str = Field(alias="from")
    to: str

    @field_serializer("days")
    def _serialize_days(self, value: list[BandwidthDay]) -> list[str]:
        return [day.value for day in value]

    @model_validator(mode="after")
    def _from_must_precede_to(self) -> BandwidthWindow:
        from_t = _parse_hhmm(self.from_, "from")
        to_t = _parse_hhmm(self.to, "to")
        if not (from_t < to_t):
            msg = f"bandwidth window 'from' ({self.from_}) must be strictly before 'to' ({self.to})"
            raise ValueError(msg)
        return self


class BandwidthConfig(BaseModel):
    """``bandwidth:`` sub-block on a transport."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    upload_mbps: float | None = None
    schedule: list[BandwidthWindow] = []

    @field_validator("upload_mbps")
    @classmethod
    def _upload_mbps_positive_or_none(cls, value: float | None) -> float | None:
        if value is not None and value <= 0:
            msg = f"upload_mbps must be > 0 when set; got {value}"
            raise ValueError(msg)
        return value


# ---------------------------------------------------------------------------
# transports
# ---------------------------------------------------------------------------


class RcloneTransport(BaseModel):
    """``transport:`` block when ``type == 'rclone'``."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    type: Literal["rclone"]
    rclone_remote: str = Field(min_length=1)
    rclone_remote_path: str = Field(min_length=1)
    bandwidth: BandwidthConfig = Field(default_factory=BandwidthConfig)


class RsyncSshTransport(BaseModel):
    """``transport:`` block when ``type == 'rsync_ssh'``.

    The model rejects any input dict that contains a ``password`` key. SSH
    password auth is forbidden by spec; only key-based auth is supported. The
    ``extra='forbid'`` setting also rejects the field, but the explicit
    ``mode='before'`` validator emits a more actionable error message.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    type: Literal["rsync_ssh"]
    ssh_target: str = Field(min_length=1)
    ssh_key_path: str = "~/.ssh/id_ed25519"
    remote_path: str = Field(min_length=1)
    bandwidth: BandwidthConfig = Field(default_factory=BandwidthConfig)

    @model_validator(mode="before")
    @classmethod
    def _reject_password_field(cls, data: Any) -> Any:
        if isinstance(data, dict) and "password" in data:
            msg = (
                "rsync_ssh transport must not declare a 'password' field; "
                "SSH password auth is unsupported. Use ssh_key_path instead."
            )
            raise ConfigError(msg)
        return data


# Discriminated union over the transport ``type`` tag. Pydantic 2 picks the
# right submodel by inspecting the ``type`` value.
EquipmentTransport = Annotated[
    RcloneTransport | RsyncSshTransport,
    Field(discriminator="type"),
]


class OrchestratorStagingTransport(BaseModel):
    """``orchestrator_staging_transport:`` -- staging hop only. Backend Spec §13."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    type: OrchestratorTransportType
    mount_point: str = Field(min_length=1)
    staging_subpath: str = Field(min_length=1)

    @field_serializer("type")
    def _serialize_type(self, value: OrchestratorTransportType) -> str:
        return value.value


# ---------------------------------------------------------------------------
# equipment
# ---------------------------------------------------------------------------


class EquipmentConfig(BaseModel):
    """One ``equipment:`` list entry. Backend Spec §9.

    ``sync_mode`` (Redesign Spec §3.2) is the per-equipment role this device
    plays for the equipment: ``nas`` means this device acquires runs and syncs
    them directly to the NAS (requires ``transport``); ``stage`` means this
    device acquires runs and pushes them to a connected PC's staging area
    (requires ``orchestrator_staging_transport``). The two transport fields
    are mutually exclusive — exactly one is populated, dictated by the mode.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str
    label: str = Field(min_length=1)
    local_root: str = Field(min_length=1)
    nas_root: str = Field(min_length=1)
    completeness_signal: CompletenessSignal
    sentinel_filename: str | None = None
    manifest_filename: str | None = None
    sync_mode: SyncMode = SyncMode.NAS
    transport: EquipmentTransport | None = None
    orchestrator_staging_transport: OrchestratorStagingTransport | None = None

    @field_serializer("completeness_signal")
    def _serialize_completeness_signal(self, value: CompletenessSignal) -> str:
        # Emit the bare string so YAML/JSON dumps round-trip the wire format.
        return value.value

    @field_serializer("sync_mode")
    def _serialize_sync_mode(self, value: SyncMode) -> str:
        return value.value

    @field_validator("id")
    @classmethod
    def _validate_equipment_id(cls, value: str) -> str:
        # Delegate to the canonical helper in paths.py so equipment-id
        # validation lives in exactly one place.
        from exlab_wizard.paths import canonicalize_equipment_id

        try:
            return canonicalize_equipment_id(value)
        except ConfigError as exc:
            raise ValueError(str(exc)) from exc

    @model_validator(mode="after")
    def _completeness_signal_requires_matching_filename(self) -> EquipmentConfig:
        match self.completeness_signal:
            case CompletenessSignal.SENTINEL_FILE:
                if not self.sentinel_filename:
                    msg = (
                        "equipment.completeness_signal == 'sentinel_file' "
                        "requires a non-empty sentinel_filename"
                    )
                    raise ValueError(msg)
            case CompletenessSignal.MANIFEST:
                if not self.manifest_filename:
                    msg = (
                        "equipment.completeness_signal == 'manifest' "
                        "requires a non-empty manifest_filename"
                    )
                    raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _sync_mode_dictates_transport(self) -> EquipmentConfig:
        match self.sync_mode:
            case SyncMode.NAS:
                if self.transport is None:
                    msg = "equipment.sync_mode == 'nas' requires a 'transport' block"
                    raise ValueError(msg)
                if self.orchestrator_staging_transport is not None:
                    msg = (
                        "equipment.sync_mode == 'nas' must not declare "
                        "'orchestrator_staging_transport'"
                    )
                    raise ValueError(msg)
            case SyncMode.STAGE:
                if self.orchestrator_staging_transport is None:
                    msg = (
                        "equipment.sync_mode == 'stage' requires an "
                        "'orchestrator_staging_transport' block"
                    )
                    raise ValueError(msg)
                if self.transport is not None:
                    msg = "equipment.sync_mode == 'stage' must not declare a 'transport' block"
                    raise ValueError(msg)
        return self


# ---------------------------------------------------------------------------
# nas_cleanup
# ---------------------------------------------------------------------------


class NASCleanupConfig(BaseModel):
    """``nas_cleanup:`` block. Local-copy retention after NAS verify."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    enabled: bool = True
    min_verify_passes: int = Field(default=2, ge=1)
    min_age_hours: int = Field(default=24, ge=0)
    retain_cache: bool = True


# ---------------------------------------------------------------------------
# logging
# ---------------------------------------------------------------------------


class LoggingConfig(BaseModel):
    """``logging:`` block. Central app-log rotation + level."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    level: str = "INFO"
    central_log_max_mb: int = Field(default=10, ge=1)
    central_log_keep: int = Field(default=5, ge=1)

    @field_validator("level", mode="before")
    @classmethod
    def _normalize_and_validate_level(cls, value: Any) -> str:
        if not isinstance(value, str):
            msg = f"logging.level must be a string; got {type(value).__name__}"
            raise ValueError(msg)
        normalized = value.strip().upper()
        if normalized not in _ALLOWED_LOG_LEVELS:
            msg = (
                f"logging.level must be one of "
                f"{sorted(_ALLOWED_LOG_LEVELS)} (case-insensitive); "
                f"got {value!r}"
            )
            raise ValueError(msg)
        return normalized


# ---------------------------------------------------------------------------
# operators
# ---------------------------------------------------------------------------


class OperatorsConfig(BaseModel):
    """``operators:`` block. Optional case-sensitive allowlist."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    allowlist: list[str] = []


# ---------------------------------------------------------------------------
# validator
# ---------------------------------------------------------------------------


def _default_content_scan_extensions() -> list[str]:
    return [
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
    ]


class ValidatorConfig(BaseModel):
    """``validator:`` block. Content-scan tuning. Backend Spec §8.1.1, §11.8."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    content_scan_max_mib: int = Field(default=5, ge=1)
    content_scan_extensions: list[str] = Field(
        default_factory=_default_content_scan_extensions,
    )

    @field_validator("content_scan_extensions")
    @classmethod
    def _extensions_must_start_with_dot(cls, value: list[str]) -> list[str]:
        for ext in value:
            if not ext.startswith("."):
                msg = f"validator.content_scan_extensions entries must start with '.'; got {ext!r}"
                raise ValueError(msg)
        return value


# ---------------------------------------------------------------------------
# plugins
# ---------------------------------------------------------------------------


class PluginsConfig(BaseModel):
    """``plugins:`` block. Master opt-in for network-declaring plugins."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    allow_network: bool = False


# ---------------------------------------------------------------------------
# sync
# ---------------------------------------------------------------------------


class SyncConfig(BaseModel):
    """``sync:`` block. NAS sync engine kill-switch + retry policy."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    enabled: bool = True
    retry_attempts: int = Field(default=3, ge=0)


# ---------------------------------------------------------------------------
# orchestrator
# ---------------------------------------------------------------------------


class OrchestratorStagingCleanup(BaseModel):
    """``orchestrator.staging_cleanup:`` sub-block. Backend Spec §13.7."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    mode: StagingCleanupMode = StagingCleanupMode.MANUAL
    # ``ge=1`` enforces the §13.7 "retain_hours > 0 when mode == 'scheduled'"
    # rule at the field level for both modes (manual ignores the value).
    retain_hours: int = Field(default=24, ge=1)

    @field_serializer("mode")
    def _serialize_mode(self, value: StagingCleanupMode) -> str:
        # Emit the bare string so YAML/JSON dumps round-trip the wire format.
        return value.value


class OrchestratorConfig(BaseModel):
    """``orchestrator:`` block. Backend Spec §9, §13.

    GUI/Orchestrator Redesign §3.1 collapsed the single-equipment /
    orchestrator distinction: the staging pipeline is always active, so
    ``label`` and ``staging_root`` become required at the top-level
    ``Config`` cross-field validator (no longer gated on a removed
    ``enabled`` toggle).
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    label: str = ""
    staging_root: str = ""
    staging_cleanup: OrchestratorStagingCleanup = Field(
        default_factory=OrchestratorStagingCleanup,
    )


# ---------------------------------------------------------------------------
# top level
# ---------------------------------------------------------------------------


class Config(BaseModel):
    """Top-level ``config.yaml`` model. Mirrors §9 verbatim."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    paths: PathsConfig = Field(default_factory=PathsConfig)
    lims: LIMSConfig = Field(default_factory=LIMSConfig)
    readme: READMEConfig = Field(default_factory=READMEConfig)
    equipment: list[EquipmentConfig] = []
    nas_cleanup: NASCleanupConfig = Field(default_factory=NASCleanupConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    operators: OperatorsConfig = Field(default_factory=OperatorsConfig)
    validator: ValidatorConfig = Field(default_factory=ValidatorConfig)
    plugins: PluginsConfig = Field(default_factory=PluginsConfig)
    sync: SyncConfig = Field(default_factory=SyncConfig)
    orchestrator: OrchestratorConfig = Field(default_factory=OrchestratorConfig)

    @model_validator(mode="after")
    def _cross_field_invariants(self) -> Config:
        # 1. Equipment IDs are unique. The ``id`` field validator already
        #    enforces the uppercase regex, so this is a strict-equality check.
        seen: set[str] = set()
        for entry in self.equipment:
            if entry.id in seen:
                msg = f"equipment IDs must be unique; duplicate {entry.id!r}"
                raise ValueError(msg)
            seen.add(entry.id)

        # 2. The staging pipeline is always active (Redesign §3.1), so
        #    label and staging_root are always required (or empty for the
        #    setup-incomplete gate to trip — see paths.setup_state).
        # The non-empty check has moved to the setup-incomplete evaluator
        # so that an in-flight first-launch config is loadable but flagged
        # for completion. Pydantic validation only ensures the fields are
        # present (which they always are due to the empty-string defaults).
        return self
