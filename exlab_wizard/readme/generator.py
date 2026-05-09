"""README generator. Backend Spec §10 / §11.4.

Renders ``README.md`` (YAML front matter + Markdown prose body) and the
``readme_fields.json`` cache file from a fully-resolved
:class:`ReadmeContext`.

The generator is the single producer for both files. The four field
layers (core / template / config / custom) are merged here and validated
before any bytes hit the disk: an out-of-bound or missing required field
raises before a partial README can be written.

Validation gates (User Interaction Spec §2 + Backend Spec §10.3):

* ``label`` non-empty after trim, ``<= LABEL_MAX_LENGTH``.
* ``operator`` non-empty after trim.
* ``objective`` non-empty after trim, ``<= OBJECTIVE_MAX_LENGTH``.
* every ``required: true`` template field has a value.
* every ``required: true`` config field has a value.
* field ids are unique across the template + config layers.
* custom field labels do not collide with the four-layer set's ids.
* core field ids (``label``, ``operator``, ``objective``) are not
  redeclared by the template or config layer (raises
  :class:`~exlab_wizard.errors.TemplateCoreFieldRedeclaredError`).
* every typed field value matches its declared type (string / text /
  choice / date / boolean).

Output format follows §10.7: a YAML front matter block delimited by
``---`` lines at the top of the file followed by a Markdown prose body.
The front matter is emitted via ``yaml.safe_dump(..., sort_keys=False)``
so the document order matches the spec example exactly.

The companion ``readme_fields.json`` is written at
``<dst>/.exlab-wizard/readme_fields.json`` using the typed
:class:`~exlab_wizard.api.schemas.ReadmeFieldsJson` Struct via
``msgspec.json.encode`` (§11.4 contract: every cache file goes through
msgspec).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from msgspec import json as msgspec_json

from exlab_wizard.api.schemas import ReadmeFieldsJson
from exlab_wizard.constants import (
    CACHE_DIR_NAME,
    LABEL_MAX_LENGTH,
    OBJECTIVE_MAX_LENGTH,
    README_FIELDS_JSON_NAME,
    README_FIELDS_JSON_VERSION,
    README_FILE_NAME,
    README_FRONT_MATTER_SCHEMA_VERSION,
)
from exlab_wizard.errors import TemplateCoreFieldRedeclaredError
from exlab_wizard.io import atomic_write_bytes
from exlab_wizard.logging import get_logger
from exlab_wizard.utils.time import dt_to_iso, parse_utc_iso

__all__ = [
    "CORE_FIELD_IDS",
    "CoreFields",
    "CustomField",
    "ReadmeContext",
    "ReadmeGenerator",
    "SystemFields",
    "TemplateFieldDecl",
]

_log = get_logger(__name__)

# The three core README field IDs. Templates and config may NOT redeclare
# these (Backend Spec §10.3). Mirrors
# ``exlab_wizard.template.copier_driver.CORE_README_FIELD_IDS`` --
# duplicated here to avoid a controller -> readme dependency cycle when
# the controller composes this generator.
CORE_FIELD_IDS: frozenset[str] = frozenset({"label", "operator", "objective"})

# Allowed values for ``TemplateFieldDecl.type``. Backend Spec §10.3.
_FIELD_TYPES: frozenset[str] = frozenset({"string", "text", "choice", "date", "boolean"})


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CoreFields:
    """Mandatory core fields (User Interaction Spec §2).

    All three are non-empty (after trim) when the controller hands the
    context to :class:`ReadmeGenerator`; the generator re-validates so a
    misuse never lets a malformed README onto disk.
    """

    label: str
    operator: str
    objective: str


@dataclass(frozen=True)
class TemplateFieldDecl:
    """Field declaration from a template's ``_exlab_readme.fields`` list
    or from ``config.yaml`` ``readme.defaults``. Backend Spec §10.3.

    The same shape covers both layers: the controller knows which list
    it came from and packs the matching :class:`ReadmeContext` slot.
    """

    id: str
    label: str
    type: Literal["string", "text", "choice", "date", "boolean"]
    required: bool = False
    default: Any = ""
    options: list[str] | None = None
    hint: str | None = None


@dataclass(frozen=True)
class CustomField:
    """An ad-hoc user-added field. Backend Spec §10.4.

    Custom fields are plain string key-value pairs (no type selection)
    and their order in the output mirrors the order the user added them.
    """

    label: str
    value: str


@dataclass(frozen=True)
class SystemFields:
    """Auto-populated, non-editable system fields. Backend Spec §10.6."""

    created: datetime
    created_by: str
    equipment: dict[str, str]
    template: dict[str, str]
    project: str
    run: str | None
    run_kind: str


@dataclass(frozen=True)
class ReadmeContext:
    """Inputs to :class:`ReadmeGenerator`. Composed by the controller.

    The controller pre-merges the four layers into the dicts below so
    the generator does not need to know about the merge order; the
    generator's job is to validate, render, and persist.
    """

    level: Literal["project", "run"]
    core: CoreFields
    template_fields: dict[str, Any]
    config_fields: dict[str, Any]
    custom_fields: list[CustomField]
    system: SystemFields
    template_field_decls: list[TemplateFieldDecl] = field(default_factory=list)
    config_field_decls: list[TemplateFieldDecl] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------


class ReadmeGenerator:
    """Renders ``README.md`` + ``readme_fields.json``. Backend Spec §10."""

    async def generate(self, dst: Path, ctx: ReadmeContext) -> tuple[Path, Path]:
        """Validate ``ctx``, write both files, return ``(readme, cache)``.

        The destination directory ``dst`` must already exist (the
        controller creates it during the directory-render phase). The
        ``.exlab-wizard/`` cache directory is created on demand.

        Both files are written via ``asyncio.to_thread`` so the asyncio
        event loop is never blocked on disk syscalls. The two writes
        share a single timestamp (``ctx.system.created``) so the
        ``generated_at`` and ``created`` fields agree.
        """
        return await asyncio.to_thread(self._generate_sync, dst, ctx)

    # ------------------------------------------------------------------
    # Sync core (runs on the worker thread)
    # ------------------------------------------------------------------

    def _generate_sync(self, dst: Path, ctx: ReadmeContext) -> tuple[Path, Path]:
        _validate(ctx)

        readme_path = dst / README_FILE_NAME
        cache_dir = dst / CACHE_DIR_NAME
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = cache_dir / README_FIELDS_JSON_NAME

        generated_at = dt_to_iso(ctx.system.created)

        front_matter = _build_front_matter(ctx, generated_at=generated_at)
        readme_bytes = _render_readme_bytes(ctx, front_matter)
        cache_payload = _build_readme_fields(ctx, generated_at=generated_at)
        cache_bytes = msgspec_json.encode(cache_payload)

        atomic_write_bytes(readme_path, readme_bytes)
        atomic_write_bytes(cache_path, cache_bytes)

        _log.info(
            "README written: %s (level=%s, run_kind=%s)",
            readme_path,
            ctx.level,
            ctx.system.run_kind,
        )
        return readme_path, cache_path


# ---------------------------------------------------------------------------
# Validation (User Interaction Spec §2 + Backend Spec §10.3)
# ---------------------------------------------------------------------------


def _validate(ctx: ReadmeContext) -> None:
    """Run every validation gate. Raises on the first failure.

    Order matches the spec's prose: core fields first, then redeclaration
    check, then required-field presence, then field-id uniqueness, then
    per-field type validation, then the custom-field label collision
    check.
    """
    _validate_core_fields(ctx.core)
    _reject_core_redeclaration(ctx.template_field_decls, layer="template")
    _reject_core_redeclaration(ctx.config_field_decls, layer="config")
    _validate_required_fields(ctx.template_field_decls, ctx.template_fields, layer="template")
    _validate_required_fields(ctx.config_field_decls, ctx.config_fields, layer="config")
    _validate_field_id_uniqueness(ctx.template_field_decls, ctx.config_field_decls)
    _validate_typed_fields(ctx.template_field_decls, ctx.template_fields, layer="template")
    _validate_typed_fields(ctx.config_field_decls, ctx.config_fields, layer="config")
    _validate_custom_field_labels(ctx)


def _validate_core_fields(core: CoreFields) -> None:
    label = core.label.strip()
    operator = core.operator.strip()
    objective = core.objective.strip()

    if not label:
        raise ValueError("core_fields.label must be non-empty after trim")
    if not operator:
        raise ValueError("core_fields.operator must be non-empty after trim")
    if not objective:
        raise ValueError("core_fields.objective must be non-empty after trim")

    if len(label) > LABEL_MAX_LENGTH:
        raise ValueError(
            f"core_fields.label exceeds {LABEL_MAX_LENGTH} characters (got {len(label)})"
        )
    if len(objective) > OBJECTIVE_MAX_LENGTH:
        raise ValueError(
            f"core_fields.objective exceeds {OBJECTIVE_MAX_LENGTH} characters "
            f"(got {len(objective)})"
        )


def _reject_core_redeclaration(
    decls: list[TemplateFieldDecl],
    *,
    layer: str,
) -> None:
    for decl in decls:
        if decl.id in CORE_FIELD_IDS:
            raise TemplateCoreFieldRedeclaredError(
                f"{layer} layer redeclares core field {decl.id!r}; "
                "core fields are backend-managed and cannot be redeclared (Backend §10.3)"
            )


def _validate_required_fields(
    decls: list[TemplateFieldDecl],
    values: dict[str, Any],
    *,
    layer: str,
) -> None:
    for decl in decls:
        if not decl.required:
            continue
        if decl.id not in values:
            raise ValueError(f"{layer}_fields[{decl.id!r}] is required but missing")
        if not _is_present(values[decl.id]):
            raise ValueError(f"{layer}_fields[{decl.id!r}] is required but empty")


def _validate_field_id_uniqueness(
    template_decls: list[TemplateFieldDecl],
    config_decls: list[TemplateFieldDecl],
) -> None:
    seen: dict[str, str] = {}
    for decl in template_decls:
        seen[decl.id] = "template"
    for decl in config_decls:
        if decl.id in seen:
            raise ValueError(
                f"field id {decl.id!r} declared in both {seen[decl.id]} and config layers"
            )
        seen[decl.id] = "config"


def _validate_typed_fields(
    decls: list[TemplateFieldDecl],
    values: dict[str, Any],
    *,
    layer: str,
) -> None:
    """Per-type validation. Spec §10.3 type semantics.

    Skips fields whose value is missing or empty unless required (the
    required-field check handles those upstream).
    """
    by_id = {d.id: d for d in decls}
    for fid, value in values.items():
        decl = by_id.get(fid)
        if decl is None:
            # Field has no declaration -- nothing to type-check against.
            continue
        if not _is_present(value) and not decl.required:
            continue
        _check_value_type(decl, value, layer=layer)


def _check_value_type(decl: TemplateFieldDecl, value: Any, *, layer: str) -> None:
    if decl.type not in _FIELD_TYPES:
        raise ValueError(
            f"{layer}_fields[{decl.id!r}] has unknown type {decl.type!r}; "
            f"allowed: {sorted(_FIELD_TYPES)}"
        )
    if decl.type in {"string", "text"}:
        if not isinstance(value, str):
            raise ValueError(
                f"{layer}_fields[{decl.id!r}] expects {decl.type}, got {type(value).__name__}"
            )
        return
    if decl.type == "choice":
        if not isinstance(value, str):
            raise ValueError(
                f"{layer}_fields[{decl.id!r}] expects choice (string), got {type(value).__name__}"
            )
        if not decl.options:
            raise ValueError(f"{layer}_fields[{decl.id!r}] is type=choice but declares no options")
        if value not in decl.options:
            raise ValueError(
                f"{layer}_fields[{decl.id!r}] value {value!r} is not in options {decl.options!r}"
            )
        return
    if decl.type == "date":
        if not isinstance(value, str):
            raise ValueError(
                f"{layer}_fields[{decl.id!r}] expects ISO 8601 date string, "
                f"got {type(value).__name__}"
            )
        try:
            parse_utc_iso(value)
        except ValueError as exc:
            raise ValueError(
                f"{layer}_fields[{decl.id!r}] is not a valid ISO 8601 date: {value!r}"
            ) from exc
        return
    # ``isinstance(True, int)`` is True in Python, so reject ints/strings
    # explicitly. ``bool`` is the only accepted shape for type=boolean.
    if decl.type == "boolean" and not isinstance(value, bool):
        raise ValueError(f"{layer}_fields[{decl.id!r}] expects bool, got {type(value).__name__}")


def _validate_custom_field_labels(ctx: ReadmeContext) -> None:
    """Ensure custom labels do not shadow any layer's field id.

    The four-layer set (Backend §10.2) reserves the core ids plus every
    template- and config-declared id. A custom label that collides would
    silently overwrite the typed value when readers ingest the front
    matter as a flat dict.
    """
    reserved: set[str] = set(CORE_FIELD_IDS)
    reserved.update(d.id for d in ctx.template_field_decls)
    reserved.update(d.id for d in ctx.config_field_decls)
    for cf in ctx.custom_fields:
        if cf.label in reserved:
            raise ValueError(
                f"custom field label {cf.label!r} collides with a "
                f"declared field id (reserved: {sorted(reserved)})"
            )


def _is_present(value: Any) -> bool:
    """Return True iff ``value`` carries a non-empty piece of content.

    Strings are trimmed; everything else (bool, list, dict, number) is
    truthy when non-empty/non-zero. ``None`` is always absent. Booleans
    are present even when ``False`` -- the user explicitly chose a value.
    """
    if value is None:
        return False
    if isinstance(value, bool):
        return True
    if isinstance(value, str):
        return bool(value.strip())
    return bool(value)


# ---------------------------------------------------------------------------
# Front matter + body rendering (Backend Spec §10.7)
# ---------------------------------------------------------------------------


def _core_fields_dict(core: CoreFields) -> dict[str, str]:
    return {"label": core.label, "operator": core.operator, "objective": core.objective}


def _custom_fields_list(custom: list[CustomField]) -> list[dict[str, str]]:
    return [{"label": cf.label, "value": cf.value} for cf in custom]


def _system_fields_dict(system: SystemFields) -> dict[str, Any]:
    return {
        "created": dt_to_iso(system.created),
        "created_by": system.created_by,
        "equipment": dict(system.equipment),
        "template": dict(system.template),
        "project": system.project,
        "run": system.run,
        "run_kind": system.run_kind,
    }


def _build_front_matter(ctx: ReadmeContext, *, generated_at: str) -> dict[str, Any]:
    """Build the ordered front matter dict for ``yaml.safe_dump``.

    Order matches the spec example at §10.7: ``schema_version``,
    ``generated_at``, ``core_fields``, ``template_fields``,
    ``config_fields``, ``custom_fields``, ``system_fields``. Empty
    layers still appear so downstream consumers can rely on the keys
    being present.
    """
    return {
        "schema_version": README_FRONT_MATTER_SCHEMA_VERSION,
        "generated_at": generated_at,
        "core_fields": _core_fields_dict(ctx.core),
        "template_fields": dict(ctx.template_fields),
        "config_fields": dict(ctx.config_fields),
        "custom_fields": _custom_fields_list(ctx.custom_fields),
        "system_fields": _system_fields_dict(ctx.system),
    }


def _render_readme_bytes(ctx: ReadmeContext, front_matter: dict[str, Any]) -> bytes:
    """Render the full README markdown into UTF-8 bytes.

    ``yaml.safe_dump`` is configured with ``sort_keys=False`` so the
    declaration order in :func:`_build_front_matter` is the on-disk
    order (the spec pins both at §10.7).
    """
    fm_text = yaml.safe_dump(
        front_matter,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )
    body = f"# {ctx.core.label}\n\n{ctx.core.objective}\n"
    document = f"---\n{fm_text}---\n\n{body}"
    return document.encode("utf-8")


def _build_readme_fields(
    ctx: ReadmeContext,
    *,
    generated_at: str,
) -> ReadmeFieldsJson:
    """Build the typed :class:`ReadmeFieldsJson` payload for the cache file."""
    return ReadmeFieldsJson(
        schema_version=README_FIELDS_JSON_VERSION,
        generated_at=generated_at,
        core_fields=_core_fields_dict(ctx.core),
        template_fields=dict(ctx.template_fields),
        config_fields=dict(ctx.config_fields),
        custom_fields=_custom_fields_list(ctx.custom_fields),
        system_fields=_system_fields_dict(ctx.system),
    )


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------


