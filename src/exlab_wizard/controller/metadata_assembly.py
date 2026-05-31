"""Shared metadata value-assembly. Backend Spec §10 / §11.3; design spec §5.

This module owns the two pure value-assembly steps that turn a creation
request into a :class:`~exlab_wizard.readme.generator.ReadmeContext` and a
:class:`~exlab_wizard.api.schemas.CreationJson`. Both the
:class:`~exlab_wizard.controller.creation.CreationController` and the
sample-data seeder call them, so the on-disk metadata they produce can
never drift between the two paths.

The helpers take explicit parameters instead of reading a controller
``self``: a :class:`Config`, the equipment id, the core fields, the
partitioned ``readme_extra``, a lightweight :class:`TemplateDesc` (a
stand-in for ``ResolvedTemplate`` so this module never imports Copier),
and injected ``created`` / ``created_by`` / ``created_at_iso`` values (so
the output is deterministic and the controller keeps stamping wall-clock
time while the seeder stamps a fixed clock).

Importantly this module MUST NOT import
``exlab_wizard.controller.creation`` -- the pure helpers it needs were
moved *here* (``_readme_decls_from_template`` / ``_readme_decls_from_config``
/ ``_os_username``) and ``creation.py`` re-imports them from this module.
"""

from __future__ import annotations

import getpass
import os
import socket
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from exlab_wizard.api.schemas import (
    CreationJson,
    LimsProjectBlock,
    OrchestratorBlock,
    PathsBlock,
    PluginApplied,
    TemplateBlock,
)
from exlab_wizard.config.models import Config
from exlab_wizard.constants import (
    CREATION_JSON_VERSION,
    CreationLevel,
    FieldType,
    RunKind,
    RunScope,
    SyncStatus,
)
from exlab_wizard.readme import (
    CoreFields,
    CustomField,
    ReadmeContext,
    SystemFields,
    TemplateFieldDecl,
)
from exlab_wizard.template.copier_driver import CORE_README_FIELD_IDS

__all__ = [
    "TemplateDesc",
    "build_creation_json",
    "build_readme_context",
]


@dataclass(frozen=True)
class TemplateDesc:
    """Dependency-light stand-in for ``ResolvedTemplate``. Design spec §5.

    Carries only the template provenance the assembly helpers need, so a
    caller can supply it without resolving a Copier template (the seeder
    passes a sentinel; the controller adapts its ``ResolvedTemplate``).

    Attributes:
        name: Template name -- maps to ``ResolvedTemplate.name``.
        version: Template version -- maps to ``ResolvedTemplate.exlab_version``.
        source_path: Stringified template source path -- maps to
            ``str(ResolvedTemplate.path)``.
        run_scope: The run-scope tag persisted on ``creation.json``'s
            template block; ``None`` for project/equipment templates.
        provenance_path: Path (relative to the instance dir, POSIX) of the
            frozen verbatim template copy written under
            ``.exlab-wizard/templates/...`` at creation time; empty when no
            copy was made.
        extra_readme_fields: ``_exlab_readme.fields`` entries (free-form
            dicts) used to build the template-layer field declarations.
        plugin_order: Plugin slug ordering (unused by these helpers but
            kept for symmetry with ``ResolvedTemplate``).
    """

    name: str
    version: str
    source_path: str
    run_scope: RunScope | None = None
    provenance_path: str = ""
    extra_readme_fields: list[dict[str, Any]] = field(default_factory=list)
    plugin_order: list[str] = field(default_factory=list)


def build_readme_context(
    *,
    config: Config,
    equipment_id: str,
    level: CreationLevel,
    label: str,
    operator: str,
    objective: str,
    readme_extra: dict[str, Any],
    template: TemplateDesc,
    short_id: str,
    run_name: str | None,
    run_kind_value: str,
    created: datetime,
    created_by: str,
) -> ReadmeContext:
    """Compose the §10 four-layer :class:`ReadmeContext`.

    Maps the template's ``_exlab_readme.fields`` and the config
    ``readme.defaults`` into typed field declarations, partitions the
    operator-supplied ``readme_extra`` values across the template /
    config / custom layers by id, and fills the auto-managed system block
    (Backend Spec §10.6) from the injected ``created`` / ``created_by``.
    """
    template_decls = _readme_decls_from_template(template.extra_readme_fields)
    config_decls = _readme_decls_from_config(config.readme.defaults)
    template_ids = {decl.id for decl in template_decls}
    config_ids = {decl.id for decl in config_decls}

    template_fields: dict[str, Any] = {}
    config_fields: dict[str, Any] = {}
    custom_fields: list[CustomField] = []
    for key, value in readme_extra.items():
        if key in template_ids:
            template_fields[key] = value
        elif key in config_ids:
            config_fields[key] = value
        elif key in CORE_README_FIELD_IDS:
            # Core fields live in their own layer; never echoed as custom.
            continue
        else:
            custom_fields.append(CustomField(label=key, value="" if value is None else str(value)))

    is_run = level is CreationLevel.RUN
    equipment = next(
        (entry for entry in config.equipment if entry.id == equipment_id),
        None,
    )
    system = SystemFields(
        created=created,
        created_by=created_by,
        equipment={"id": equipment_id, "label": equipment.label if equipment else ""},
        template={"name": template.name, "version": template.version},
        # §10.6: ``project`` is the machine-safe LIMS short id recorded in
        # README metadata (§3.1) -- distinct from the human-readable
        # ``<project>/`` folder segment. ``run`` is the run directory name.
        project=short_id,
        run=run_name if is_run else None,
        run_kind=run_kind_value if is_run else "",
    )
    return ReadmeContext(
        level=level,
        core=CoreFields(label=label, operator=operator, objective=objective),
        template_fields=template_fields,
        config_fields=config_fields,
        custom_fields=custom_fields,
        system=system,
        template_field_decls=template_decls,
        config_field_decls=config_decls,
    )


def build_creation_json(
    *,
    config: Config,
    equipment_id: str,
    operator: str,
    level: CreationLevel,
    run_kind_value: str,
    lims_block: LimsProjectBlock,
    template: TemplateDesc,
    variables: dict[str, Any],
    dst: Path,
    nas_root: str,
    plugins_applied: list[PluginApplied],
    created_at_iso: str,
    sync_status: SyncStatus = SyncStatus.PENDING,
) -> CreationJson:
    """Assemble the §11.3 :class:`CreationJson` payload.

    Redesign §3.1: creation.json always carries the orchestrator block.
    Redesign §3.3: the block carries the producing equipment's label so a
    receiving orchestrator can auto-discover the relayed equipment without
    a per-equipment config of its own. ``sync_status`` is injectable
    (controller passes ``PENDING``; the seeder passes the per-run
    scenario) and ``created_at`` is the injected ``created_at_iso``.
    """
    eq = next((e for e in config.equipment if e.id == equipment_id), None)
    orchestrator_block = OrchestratorBlock(
        enabled=True,
        host=socket.gethostname(),
        label=config.orchestrator.label,
        equipment_label=eq.label if eq else None,
    )

    return CreationJson(
        schema_version=CREATION_JSON_VERSION,
        created_at=created_at_iso,
        created_by=operator,
        level=level,
        run_kind=RunKind(run_kind_value),
        lims_project=lims_block,
        template=TemplateBlock(
            name=template.name,
            version=template.version,
            source_path=template.source_path,
            run_scope=template.run_scope,
            provenance_path=template.provenance_path,
        ),
        variables=dict(variables),
        paths=PathsBlock(
            local=str(dst),
            nas=str(Path(nas_root) / equipment_id) if nas_root else "",
        ),
        plugins_applied=plugins_applied,
        orchestrator=orchestrator_block,
        sync_status=sync_status,
    )


# ---------------------------------------------------------------------------
# Pure helpers (moved out of controller/creation.py)
# ---------------------------------------------------------------------------


def _readme_decls_from_template(entries: list[dict[str, Any]]) -> list[TemplateFieldDecl]:
    """Map a template's ``_exlab_readme.fields`` dicts to typed declarations.

    Entries without a string ``id`` are skipped (mirrors
    :func:`_required_field_ids`); ``type`` is coerced to
    :class:`~exlab_wizard.constants.FieldType` so the generator can
    type-check values against it. An unknown ``type`` raises ``ValueError``,
    which the pipeline surfaces as a failed creation.
    """
    decls: list[TemplateFieldDecl] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        fid = entry.get("id")
        if not isinstance(fid, str) or not fid:
            continue
        options = entry.get("options")
        hint = entry.get("hint")
        decls.append(
            TemplateFieldDecl(
                id=fid,
                label=str(entry.get("label", fid)),
                type=FieldType(str(entry.get("type", FieldType.STRING.value))),
                required=bool(entry.get("required", False)),
                default=entry.get("default", ""),
                options=list(options) if isinstance(options, list) else None,
                hint=hint if isinstance(hint, str) else None,
            )
        )
    return decls


def _readme_decls_from_config(defaults: list[Any]) -> list[TemplateFieldDecl]:
    """Map ``config.readme.defaults`` entries to typed declarations.

    Core field ids are dropped -- they are backend-managed and live in
    their own layer (Backend Spec §10.3), matching the required-field gate
    in :meth:`CreationController._validate_inputs`.
    """
    decls: list[TemplateFieldDecl] = []
    for entry in defaults:
        if entry.id in CORE_README_FIELD_IDS:
            continue
        decls.append(
            TemplateFieldDecl(
                id=entry.id,
                label=entry.label,
                type=entry.type,
                required=entry.required,
                default=entry.default,
                options=list(entry.options) if entry.options else None,
                hint=entry.hint,
            )
        )
    return decls


def _os_username() -> str:
    """Return the creating OS user for the README ``system.created_by``.

    Distinct from the experiment ``operator`` (Backend Spec §10.6). Falls
    back to the ``USER`` / ``USERNAME`` environment variables and finally
    ``"unknown"`` when the platform cannot report a login name.
    """
    try:
        return getpass.getuser()
    except Exception:
        return os.environ.get("USER") or os.environ.get("USERNAME") or "unknown"
