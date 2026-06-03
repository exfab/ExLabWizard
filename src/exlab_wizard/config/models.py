"""Pydantic models that mirror ``config.yaml``. Backend Spec §9.

These models are the typed schema for the on-disk ``config.yaml``. The loader
(``exlab_wizard.config.loader``) parses YAML into a plain ``dict``, hands it to
``Config.model_validate``, and converts any Pydantic ``ValidationError`` into a
``ConfigError`` at the boundary; nothing here raises ``ConfigError`` directly
except for cases that need a custom message before the model layer sees the
input.

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
from pathlib import Path
from typing import Any

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
    FieldType,
    StagingCleanupMode,
    SyncMode,
)
from exlab_wizard.errors import ConfigError
from exlab_wizard.paths import default_app_root

__all__ = [
    "BandwidthConfig",
    "BandwidthWindow",
    "Config",
    "EquipmentConfig",
    "FileStabilityConfig",
    "LIMSConfig",
    "LoggingConfig",
    "NASCleanupConfig",
    "NasConfig",
    "OperatorsConfig",
    "OrchestratorConfig",
    "OrchestratorStagingCleanup",
    "PathsConfig",
    "PluginsConfig",
    "READMEConfig",
    "READMEDefaultField",
    "RclonePerf",
    "SyncConfig",
    "UpdateCheckConfig",
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
    """``paths:`` block. A single app root with derived working subdirectories.

    Only ``app_root`` is stored (and serialized); ``templates/``, ``plugins/``
    and the experiment ``data/`` root are *derived* read-only properties so the
    operator configures exactly one location. ``app_root`` defaults under the OS
    *Documents* folder (``<Documents>/ExLabWizard`` via
    :func:`exlab_wizard.paths.default_app_root`) so a fresh install needs no
    manual path entry.

    The derived names ``templates_dir`` / ``plugin_dir`` / ``local_root`` are
    kept so existing read-only consumers (run creation, browse, template
    resolution) keep reading ``config.paths.local_root`` unchanged -- it now
    resolves to ``<app_root>/data``. ``local_root`` is an alias of
    ``data_root``; new code should prefer ``data_root``.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    app_root: str = Field(default_factory=lambda: str(default_app_root()))

    @property
    def data_root(self) -> str:
        """The experiment data root, ``<app_root>/data``."""
        return str(Path(self.app_root) / "data")

    @property
    def local_root(self) -> str:
        """Alias of :attr:`data_root` (kept for existing consumers)."""
        return self.data_root

    @property
    def templates_dir(self) -> str:
        """The global Copier template library, ``<app_root>/templates``."""
        return str(Path(self.app_root) / "templates")

    @property
    def plugin_dir(self) -> str:
        """The lab plugin directory, ``<app_root>/plugins``."""
        return str(Path(self.app_root) / "plugins")


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
# nas
# ---------------------------------------------------------------------------


class RclonePerf(BaseModel):
    """Parallelism knobs forwarded to rclone (``--transfers`` / ``--checkers``).

    These double as the memory dial on space- and RAM-constrained
    acquisition machines: peak memory scales with these counts times
    rclone's per-stream buffer.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    transfers: int = Field(default=4, ge=1, le=64)
    checkers: int = Field(default=8, ge=1, le=64)


class NasConfig(BaseModel):
    """``nas:`` block — the single rclone remote + base root for NAS sync.

    ``remote`` is the name of a remote defined in the operator's
    ``rclone.conf`` (set up separately with ``rclone config``). Equipment
    run folders live under ``<remote>:<base_root>/<equipment_id>/…``.
    ``rclone_config_path`` optionally pins ``rclone --config <path>`` for
    when the app runs as a different OS user than the one who created the
    config; blank means rclone's default discovery.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    remote: str = ""
    base_root: str = ""
    rclone_config_path: str = ""
    # Reconcile tolerance: a file counts as synced only when its remote modtime
    # is within this many seconds of local (absorbs SFTP/SMB modtime rounding).
    mtime_tolerance_s: int = Field(default=2, ge=0)
    perf: RclonePerf = Field(default_factory=RclonePerf)
    bandwidth: BandwidthConfig = Field(default_factory=BandwidthConfig)


# ---------------------------------------------------------------------------
# equipment
# ---------------------------------------------------------------------------


class EquipmentConfig(BaseModel):
    """One ``equipment:`` list entry. Backend Spec §9.

    ``sync_mode`` (Redesign Spec §3.2) is the per-equipment role this device
    plays for the equipment: ``nas`` means this device acquires runs and syncs
    them directly to the NAS; ``stage`` means this device acquires runs and
    pushes them to a connected staging PC's staging area instead.

    rclone.conf NAS-sync migration: neither mode carries a per-equipment
    connection block. The NAS connection is defined once by the ``nas:`` block
    (a single rclone remote); the staging hop is defined once by
    ``orchestrator.staging_remote`` / ``orchestrator.staging_base_root`` (a
    second rclone remote in the same ``rclone.conf``). The push target is
    selected by ``sync_mode`` at sync time, not by a per-equipment block.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str
    label: str = Field(min_length=1)
    nas_root: str = Field(min_length=1)
    sync_mode: SyncMode = SyncMode.NAS

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
    delete_ignored: bool = False


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


def _default_ignore_globs() -> list[str]:
    return ["*.partial", "*.tmp"]


class FileStabilityConfig(BaseModel):
    """``sync.stability:`` sub-block. Pre-rclone size-stability guard.

    A complete file passes in ``(checks - 1) * interval_seconds`` (~4 s with
    the defaults); ``timeout_seconds`` bounds a still-growing file before it
    is deferred to a later sweep. On NFS-mounted sources raise
    ``interval_seconds`` to at least the mount's ``actimeo`` (typically >= 30 s)
    so attribute-cache staleness cannot mask an in-progress write.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    enabled: bool = True
    interval_seconds: float = Field(default=2.0, gt=0)
    checks: int = Field(default=3, ge=2)
    timeout_seconds: float = Field(default=30.0, gt=0)
    max_workers: int = Field(default=8, ge=1)


class SyncConfig(BaseModel):
    """``sync:`` block. NAS sync engine kill-switch + retry / quiescence policy."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    enabled: bool = True
    retry_attempts: int = Field(default=3, ge=0)
    quiescence_minutes: int = Field(default=10, ge=1)
    ignore_globs: list[str] = Field(default_factory=_default_ignore_globs)
    poll_interval_seconds: int = Field(default=120, ge=1)
    stability: FileStabilityConfig = Field(default_factory=FileStabilityConfig)


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
    orchestrator distinction (no ``enabled`` toggle). ``label`` is required
    by the setup-state gate -- it identifies this workstation in every run's
    ``creation.json``. ``staging_root`` is **opt-in**: a blank value means
    this device is not a staging PC, so it does not gate setup and no staging
    directory is created until the operator saves a non-empty path.

    rclone.conf NAS-sync migration (Phase 8): ``staging_remote`` /
    ``staging_base_root`` define the orchestrator's stage-mode hop as a named
    rclone remote (a second remote in the same ``rclone.conf`` as the
    ``nas:`` remote). stage-mode equipment push run folders to
    ``<staging_remote>:<staging_base_root>/<equipment_id>/<run-leaf>`` using
    the same :class:`RcloneDriver` ops as the NAS leg. ``staging_perf`` is the
    parallelism dial for that hop.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    label: str = ""
    staging_root: str = ""
    staging_remote: str = ""
    staging_base_root: str = ""
    staging_perf: RclonePerf = Field(default_factory=RclonePerf)
    staging_cleanup: OrchestratorStagingCleanup = Field(
        default_factory=OrchestratorStagingCleanup,
    )


# ---------------------------------------------------------------------------
# update_check
# ---------------------------------------------------------------------------


class UpdateCheckConfig(BaseModel):
    """``update_check:`` block. Design Spec §15.6 / §15.8 item 3.

    Kill-switch for the startup update notifier (the §15.8 item 3 self-update
    channel, notifier-only stage). Default-ON; set ``enabled: false`` to stop
    the launch-time GitHub Releases probe on air-gapped or policy-locked hosts.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    enabled: bool = True


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
    nas: NasConfig = Field(default_factory=NasConfig)
    nas_cleanup: NASCleanupConfig = Field(default_factory=NASCleanupConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    operators: OperatorsConfig = Field(default_factory=OperatorsConfig)
    validator: ValidatorConfig = Field(default_factory=ValidatorConfig)
    plugins: PluginsConfig = Field(default_factory=PluginsConfig)
    sync: SyncConfig = Field(default_factory=SyncConfig)
    orchestrator: OrchestratorConfig = Field(default_factory=OrchestratorConfig)
    update_check: UpdateCheckConfig = Field(default_factory=UpdateCheckConfig)

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

        # 2. Orchestrator identity: ``label`` is required and ``staging_root``
        #    is opt-in (a blank value just means this device is not a staging
        #    PC). The non-empty ``label`` check lives in the setup-incomplete
        #    evaluator (see paths.setup_state) so an in-flight first-launch
        #    config is loadable but flagged for completion. Pydantic only
        #    ensures the fields are present (always true via empty-string
        #    defaults).
        return self


def config_with_equipment_appended(config: Config | None, equipment: EquipmentConfig) -> Config:
    """Return a copy of ``config`` with ``equipment`` appended.

    The single place the Add-Equipment flow merges a new device into the
    live config -- shared by the ``POST /config/equipment`` route and the
    NiceGUI wizard's confirm step so both reject duplicate ids and re-run
    the same cross-field validation instead of open-coding the merge
    twice. ``config`` may be ``None`` on a fresh install that has no
    ``config.yaml`` yet, in which case a default :class:`Config` is the
    base.

    Raises :class:`exlab_wizard.errors.ConfigError` when an equipment
    entry with the same id already exists.
    """
    base = config or Config()
    for entry in base.equipment:
        if entry.id == equipment.id:
            msg = f"equipment id {equipment.id!r} already exists in config"
            raise ConfigError(msg)
    merged = base.model_copy(update={"equipment": [*base.equipment, equipment]})
    # Re-run the full cross-field validation (unique ids, etc.) on the
    # merged result so a bad merge fails loudly rather than persisting.
    return Config.model_validate(merged.model_dump(mode="python"))
