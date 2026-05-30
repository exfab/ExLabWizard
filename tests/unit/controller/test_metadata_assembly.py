"""Unit + parity tests for ``controller/metadata_assembly``.

The two helpers ``build_readme_context`` / ``build_creation_json`` were
extracted out of :class:`CreationController` so the controller and the
forthcoming sample-data seeder share one assembly layer and can never
drift (design spec §5). These tests pin the helper behaviour directly and
assert parity with the controller methods that now delegate to them.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import msgspec

from exlab_wizard.api.schemas import (
    LimsProjectBlock,
    PluginApplied,
)
from exlab_wizard.cache.creation_writer import CreationWriter
from exlab_wizard.cache.equipment import EquipmentCacheWriter
from exlab_wizard.config.models import (
    Config,
    EquipmentConfig,
    OperatorsConfig,
    PathsConfig,
    READMEConfig,
    READMEDefaultField,
)
from exlab_wizard.constants import (
    CREATION_JSON_VERSION,
    CreationLevel,
    FieldType,
    LIMSProjectSource,
    PluginStatus,
    RunKind,
    RunScope,
    SyncStatus,
)
from exlab_wizard.controller.creation import (
    CreationController,
    NoOpNASSync,
    NoOpReadmeGenerator,
    ProjectCreateRequest,
    SessionStore,
)
from exlab_wizard.controller.metadata_assembly import (
    TemplateDesc,
    build_creation_json,
    build_readme_context,
)
from exlab_wizard.readme import CustomField
from exlab_wizard.template.copier_driver import TemplateEngine
from exlab_wizard.validator.engine import Validator

FIXTURE_TEMPLATES = Path(__file__).parent.parent.parent / "fixtures" / "templates"
FIXTURE_PLUGINS = Path(__file__).parent.parent.parent / "fixtures" / "plugins"

FIXED_CREATED = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
FIXED_CREATED_BY = "fixture-os-user"
FIXED_CREATED_AT_ISO = "2026-01-01T09:00:00+00:00"


# ---------------------------------------------------------------------------
# Fixtures / builders
# ---------------------------------------------------------------------------


def _config(local_root: Path, *, defaults: list[READMEDefaultField] | None = None) -> Config:
    return Config(
        paths=PathsConfig(
            templates_dir=str(FIXTURE_TEMPLATES),
            plugin_dir=str(FIXTURE_PLUGINS),
            local_root=str(local_root),
        ),
        equipment=[
            EquipmentConfig(
                id="EQ1",
                label="Equipment One",
                local_root=str(local_root),
                nas_root="/srv/nas",
            )
        ],
        operators=OperatorsConfig(allowlist=[]),
        readme=READMEConfig(defaults=defaults or []),
    )


def _template_desc() -> TemplateDesc:
    return TemplateDesc(
        name="seed",
        version="0",
        source_path="",
        run_scope=RunScope.BOTH,
        extra_readme_fields=[],
        plugin_order=[],
    )


def _sample_type_default() -> READMEDefaultField:
    return READMEDefaultField(
        id="sample_type",
        label="Sample type",
        type=FieldType.CHOICE,
        required=False,
        default="",
        options=["control", "treatment"],
    )


# ---------------------------------------------------------------------------
# build_readme_context — direct unit tests
# ---------------------------------------------------------------------------


def test_build_readme_context_partitions_readme_extra(tmp_path: Path) -> None:
    """``readme_extra`` keys split across template / config / custom layers,
    and core ids are skipped entirely."""
    config = _config(tmp_path, defaults=[_sample_type_default()])
    desc = dataclasses.replace(
        _template_desc(),
        extra_readme_fields=[{"id": "hypothesis", "type": "text", "label": "Hypothesis"}],
    )
    ctx = build_readme_context(
        config=config,
        equipment_id="EQ1",
        level=CreationLevel.PROJECT,
        label="My Project",
        operator="asmith",
        objective="Do science.",
        readme_extra={
            "hypothesis": "cells respond",  # template layer
            "sample_type": "control",  # config layer
            "reviewer": "bjones",  # custom layer
            "label": "ignored-core",  # core id -> skipped
        },
        template=desc,
        short_id="PROJ-0001",
        run_name=None,
        run_kind_value="",
        created=FIXED_CREATED,
        created_by=FIXED_CREATED_BY,
    )

    assert ctx.template_fields == {"hypothesis": "cells respond"}
    assert ctx.config_fields == {"sample_type": "control"}
    assert ctx.custom_fields == [CustomField(label="reviewer", value="bjones")]
    # The core id never leaks into custom_fields.
    assert all(f.label != "label" for f in ctx.custom_fields)


def test_build_readme_context_system_and_level_for_run(tmp_path: Path) -> None:
    config = _config(tmp_path)
    ctx = build_readme_context(
        config=config,
        equipment_id="EQ1",
        level=CreationLevel.RUN,
        label="Baseline",
        operator="asmith",
        objective="Acquire baseline.",
        readme_extra={},
        template=_template_desc(),
        short_id="PROJ-0001",
        run_name="Run_2026-01-01T09-00",
        run_kind_value=RunKind.EXPERIMENTAL.value,
        created=FIXED_CREATED,
        created_by=FIXED_CREATED_BY,
    )

    assert ctx.level is CreationLevel.RUN
    assert ctx.core.label == "Baseline"
    assert ctx.core.operator == "asmith"
    assert ctx.core.objective == "Acquire baseline."
    assert ctx.system.created == FIXED_CREATED
    assert ctx.system.created_by == FIXED_CREATED_BY
    assert ctx.system.equipment == {"id": "EQ1", "label": "Equipment One"}
    assert ctx.system.template == {"name": "seed", "version": "0"}
    assert ctx.system.project == "PROJ-0001"
    assert ctx.system.run == "Run_2026-01-01T09-00"
    assert ctx.system.run_kind == RunKind.EXPERIMENTAL.value


def test_build_readme_context_unknown_equipment_blank_label(tmp_path: Path) -> None:
    config = _config(tmp_path)
    ctx = build_readme_context(
        config=config,
        equipment_id="NOPE",
        level=CreationLevel.PROJECT,
        label="P",
        operator="o",
        objective="obj",
        readme_extra={},
        template=_template_desc(),
        short_id="PROJ-0001",
        run_name=None,
        run_kind_value="",
        created=FIXED_CREATED,
        created_by=FIXED_CREATED_BY,
    )
    assert ctx.system.equipment == {"id": "NOPE", "label": ""}
    assert ctx.system.run is None


# ---------------------------------------------------------------------------
# build_creation_json — direct unit tests
# ---------------------------------------------------------------------------


def _lims_block() -> LimsProjectBlock:
    return LimsProjectBlock(
        uid="",
        short_id="PROJ-0001",
        name_at_creation="Demo Project",
        source=LIMSProjectSource.LIVE,
    )


def test_build_creation_json_all_blocks_populated(tmp_path: Path) -> None:
    config = _config(tmp_path)
    dst = tmp_path / "EQ1" / "Demo Project"
    plugins = [
        PluginApplied(
            plugin="p",
            version="1.0",
            files_affected=["a.txt"],
            status=PluginStatus.SUCCESS,
        ),
    ]
    payload = build_creation_json(
        config=config,
        equipment_id="EQ1",
        operator="asmith",
        level=CreationLevel.PROJECT,
        run_kind_value=RunKind.EXPERIMENTAL.value,
        lims_block=_lims_block(),
        template=dataclasses.replace(
            _template_desc(), name="basic", version="1.2", source_path="/tpl/basic"
        ),
        variables={"k": "v"},
        dst=dst,
        nas_root="/srv/nas",
        plugins_applied=plugins,
        created_at_iso=FIXED_CREATED_AT_ISO,
    )

    assert payload.schema_version == CREATION_JSON_VERSION
    assert payload.created_at == FIXED_CREATED_AT_ISO
    assert payload.created_by == "asmith"
    assert payload.level == CreationLevel.PROJECT
    assert payload.run_kind == RunKind.EXPERIMENTAL.value
    assert payload.lims_project.short_id == "PROJ-0001"
    assert payload.template.name == "basic"
    assert payload.template.version == "1.2"
    assert payload.template.source_path == "/tpl/basic"
    assert payload.template.run_scope == RunScope.BOTH.value
    assert payload.variables == {"k": "v"}
    assert payload.paths.local == str(dst)
    assert payload.paths.nas == str(Path("/srv/nas") / "EQ1")
    assert payload.plugins_applied == plugins
    assert payload.orchestrator is not None
    assert payload.orchestrator.enabled is True
    assert payload.orchestrator.equipment_label == "Equipment One"
    # Default sync status.
    assert payload.sync_status == SyncStatus.PENDING.value


def test_build_creation_json_sync_status_override(tmp_path: Path) -> None:
    config = _config(tmp_path)
    payload = build_creation_json(
        config=config,
        equipment_id="EQ1",
        operator="asmith",
        level=CreationLevel.RUN,
        run_kind_value=RunKind.TEST.value,
        lims_block=_lims_block(),
        template=_template_desc(),
        variables={},
        dst=tmp_path / "run",
        nas_root="/srv/nas",
        plugins_applied=[],
        sync_status=SyncStatus.SYNCED,
        created_at_iso=FIXED_CREATED_AT_ISO,
    )
    assert payload.sync_status == SyncStatus.SYNCED.value
    assert payload.run_kind == RunKind.TEST.value


def test_build_creation_json_blank_nas_when_root_empty(tmp_path: Path) -> None:
    config = _config(tmp_path)
    payload = build_creation_json(
        config=config,
        equipment_id="EQ1",
        operator="asmith",
        level=CreationLevel.PROJECT,
        run_kind_value=RunKind.EXPERIMENTAL.value,
        lims_block=_lims_block(),
        template=_template_desc(),
        variables={},
        dst=tmp_path / "p",
        nas_root="",
        plugins_applied=[],
        created_at_iso=FIXED_CREATED_AT_ISO,
    )
    assert payload.paths.nas == ""


# ---------------------------------------------------------------------------
# Parity: controller methods vs standalone helpers
# ---------------------------------------------------------------------------


def _controller(config: Config) -> CreationController:
    return CreationController(
        config=config,
        validator=Validator(),
        template_engine=TemplateEngine(),
        plugin_host=None,
        cache_creation=CreationWriter(),
        cache_equipment=EquipmentCacheWriter(),
        readme_generator=NoOpReadmeGenerator(),
        nas_sync=NoOpNASSync(),
        session_store=SessionStore(),
    )


def _project_request() -> ProjectCreateRequest:
    return ProjectCreateRequest(
        equipment_id="EQ1",
        template_path=FIXTURE_TEMPLATES / "project_basic",
        lims_project={
            "uid": "8c7e9d2f-1a4b-4e6c-9b3d-7f2a1e5d8c4b",
            "short_id": "PROJ-0042",
            "name_at_creation": "Cortex Q3 Pilot",
            "source": "live",
        },
        variables={"_exlab_proj": "PROJ-0042"},
        label="Cortex Q3 calibration",
        operator="asmith",
        objective="First-pass calibration.",
        readme_extra={"sample_type": "control", "reviewer": "bjones"},
    )


def _resolved_desc_from(resolved: Any) -> TemplateDesc:
    return TemplateDesc(
        name=resolved.name,
        version=resolved.exlab_version,
        source_path=str(resolved.path),
        run_scope=resolved.run_scope,
        extra_readme_fields=resolved.extra_readme_fields,
        plugin_order=resolved.plugin_order,
    )


def _normalize_ctx(ctx: Any) -> Any:
    """Replace the injected, environment-dependent system fields with
    fixtures so two contexts assembled at different instants compare."""
    system = dataclasses.replace(ctx.system, created=FIXED_CREATED, created_by=FIXED_CREATED_BY)
    return dataclasses.replace(ctx, system=system)


def test_parity_build_readme_context(tmp_path: Path) -> None:
    local_root = tmp_path / "data"
    local_root.mkdir()
    config = _config(local_root, defaults=[_sample_type_default()])
    controller = _controller(config)

    req = _project_request()
    resolved = controller._resolve_template(req)
    dst = local_root / "EQ1" / "Cortex Q3 Pilot"

    controller_ctx = controller._build_readme_context(req=req, resolved=resolved, dst=dst)

    helper_ctx = build_readme_context(
        config=config,
        equipment_id=req.equipment_id,
        level=CreationLevel.PROJECT,
        label=req.label,
        operator=req.operator,
        objective=req.objective,
        readme_extra=req.readme_extra,
        template=_resolved_desc_from(resolved),
        short_id=CreationController._short_id_for(req),
        run_name=None,
        run_kind_value="",
        created=FIXED_CREATED,
        created_by=FIXED_CREATED_BY,
    )

    assert _normalize_ctx(controller_ctx) == helper_ctx


async def test_parity_build_creation_json(tmp_path: Path) -> None:
    local_root = tmp_path / "data"
    local_root.mkdir()
    config = _config(local_root, defaults=[_sample_type_default()])
    controller = _controller(config)

    req = _project_request()
    resolved = controller._resolve_template(req)
    dst = local_root / "EQ1" / "Cortex Q3 Pilot"
    dst.mkdir(parents=True)

    from exlab_wizard.plugins.host import PluginPassResult
    from exlab_wizard.template.copier_driver import RenderResult

    session = controller.session_store.open("project", req)
    controller_payload = await controller._write_cache(
        session=session,
        req=req,
        resolved=resolved,
        dst=dst,
        render_result=RenderResult(dst_path=dst, files_written=[]),
        plugin_result=PluginPassResult(applied=[], aborted=False),
    )

    helper_payload = build_creation_json(
        config=config,
        equipment_id=req.equipment_id,
        operator=req.operator,
        level=CreationLevel.PROJECT,
        run_kind_value=RunKind.EXPERIMENTAL.value,
        lims_block=LimsProjectBlock(
            uid=str(req.lims_project["uid"]),
            short_id=str(req.lims_project["short_id"]),
            name_at_creation="Cortex Q3 Pilot",
            source=LIMSProjectSource.LIVE,
        ),
        template=_resolved_desc_from(resolved),
        variables=req.variables,
        dst=dst,
        nas_root="/srv/nas",
        plugins_applied=[],
        sync_status=SyncStatus.PENDING,
        created_at_iso=FIXED_CREATED_AT_ISO,
    )

    # Normalize the injected timestamp before comparing the encoded form.
    norm_controller = msgspec.structs.replace(controller_payload, created_at=FIXED_CREATED_AT_ISO)
    assert msgspec.json.encode(norm_controller) == msgspec.json.encode(helper_payload)
