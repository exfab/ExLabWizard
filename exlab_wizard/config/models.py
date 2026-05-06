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
- ``StrEnum`` values are accepted in either string or enum form via the
  ``Literal[...]`` annotations; the spec stores the string value verbatim.
- All cross-field invariants from §9 are encoded as ``model_validator``s.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from exlab_wizard.constants import (
    EQUIPMENT_ID_MAX_LENGTH,
    EQUIPMENT_ID_PATTERN,
    TEMPLATE_QUESTION_ID_PATTERN,
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

# Strict day-of-week vocabulary used by ``BandwidthWindow.days``.
_BandwidthDay = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _parse_hhmm(value: str, field_name: str) -> int:
    """Return minutes-since-midnight for an ``HH:MM`` string.

    Validation:
    - Must be exactly 5 characters: ``HH:MM`` with a literal ``:``.
    - Hours in ``[0, 23]``; minutes in ``[0, 59]``.
    Raises ``ValueError`` so Pydantic surfaces a normal validation error.
    """
    if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
        msg = f"{field_name} must be a zero-padded HH:MM string, got {value!r}"
        raise ValueError(msg)
    try:
        hour = int(value[:2])
        minute = int(value[3:])
    except ValueError as exc:
        msg = f"{field_name} must be a zero-padded HH:MM string, got {value!r}"
        raise ValueError(msg) from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        msg = f"{field_name} must be a valid time in HH:MM, got {value!r}"
        raise ValueError(msg)
    return hour * 60 + minute


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
    type: Literal["string", "text", "choice", "date", "boolean"]
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

    @model_validator(mode="after")
    def _choice_requires_non_empty_options(self) -> READMEDefaultField:
        match self.type:
            case "choice":
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

    days: list[_BandwidthDay] = Field(min_length=1)
    from_: str = Field(alias="from")
    to: str

    @field_validator("from_", "to")
    @classmethod
    def _validate_hhmm_format(cls, value: str) -> str:
        # Validate the HH:MM grammar; the from < to check happens after.
        _parse_hhmm(value, "bandwidth window time")
        return value

    @model_validator(mode="after")
    def _from_must_precede_to(self) -> BandwidthWindow:
        from_minutes = _parse_hhmm(self.from_, "from")
        to_minutes = _parse_hhmm(self.to, "to")
        if not (from_minutes < to_minutes):
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

    type: Literal["smb_mount", "file_transfer"]
    mount_point: str = Field(min_length=1)
    staging_subpath: str = Field(min_length=1)


# ---------------------------------------------------------------------------
# equipment
# ---------------------------------------------------------------------------


class EquipmentConfig(BaseModel):
    """One ``equipment:`` list entry. Backend Spec §9."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str
    label: str = Field(min_length=1)
    local_root: str = Field(min_length=1)
    nas_root: str = Field(min_length=1)
    completeness_signal: Literal["sentinel_file", "manifest"]
    sentinel_filename: str | None = None
    manifest_filename: str | None = None
    transport: EquipmentTransport
    orchestrator_staging_transport: OrchestratorStagingTransport | None = None

    @field_validator("id")
    @classmethod
    def _validate_equipment_id(cls, value: str) -> str:
        if len(value) > EQUIPMENT_ID_MAX_LENGTH:
            msg = (
                f"equipment.id {value!r} exceeds max length "
                f"{EQUIPMENT_ID_MAX_LENGTH} ({len(value)} chars)"
            )
            raise ValueError(msg)
        if not EQUIPMENT_ID_PATTERN.fullmatch(value):
            msg = f"equipment.id {value!r} does not match {EQUIPMENT_ID_PATTERN.pattern}"
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _completeness_signal_requires_matching_filename(self) -> EquipmentConfig:
        match self.completeness_signal:
            case "sentinel_file":
                if not self.sentinel_filename:
                    msg = (
                        "equipment.completeness_signal == 'sentinel_file' "
                        "requires a non-empty sentinel_filename"
                    )
                    raise ValueError(msg)
            case "manifest":
                if not self.manifest_filename:
                    msg = (
                        "equipment.completeness_signal == 'manifest' "
                        "requires a non-empty manifest_filename"
                    )
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
            if not isinstance(ext, str) or not ext.startswith("."):
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

    mode: Literal["manual", "scheduled"] = "manual"
    retain_hours: int = Field(default=24, ge=1)

    @model_validator(mode="after")
    def _scheduled_requires_positive_retain_hours(self) -> OrchestratorStagingCleanup:
        # ge=1 already enforces > 0; the explicit check makes the spec rule
        # legible at the call site.
        match self.mode:
            case "scheduled":
                if self.retain_hours <= 0:
                    msg = (
                        "orchestrator.staging_cleanup.retain_hours must be "
                        "> 0 when mode == 'scheduled'"
                    )
                    raise ValueError(msg)
            case _:
                pass
        return self


class OrchestratorConfig(BaseModel):
    """``orchestrator:`` block. Backend Spec §9, §13."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    enabled: bool = False
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

        # 2. When orchestrator is enabled, label and staging_root must be set.
        if self.orchestrator.enabled:
            if not self.orchestrator.label:
                msg = (
                    "orchestrator.label must be a non-empty string when "
                    "orchestrator.enabled is true"
                )
                raise ValueError(msg)
            if not self.orchestrator.staging_root:
                msg = (
                    "orchestrator.staging_root must be a non-empty string when "
                    "orchestrator.enabled is true"
                )
                raise ValueError(msg)
        return self
