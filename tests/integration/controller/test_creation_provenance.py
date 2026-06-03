"""Integration test for the frozen template provenance copy (Phase 3b).

Drives a full project + run creation through :class:`CreationController`
and asserts that the run's instance directory carries a verbatim copy of
the run template source under ``.exlab-wizard/templates/run/<name>/``,
that ``creation.json`` records the instance-relative provenance path, and
that the copied ``copier.yml`` / ``.jinja`` files do NOT trip the
post-validate gate (the session reaches DONE and is not
``BLOCKED_BY_VALIDATION``).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import msgspec

from exlab_wizard.api.schemas import CreationJson
from exlab_wizard.cache.creation_writer import CreationWriter
from exlab_wizard.cache.equipment import EquipmentCacheWriter
from exlab_wizard.config.models import (
    Config,
    EquipmentConfig,
    OperatorsConfig,
    PathsConfig,
    READMEConfig,
)
from exlab_wizard.constants import (
    CACHE_DIR_NAME,
    CREATION_JSON_NAME,
    CREATION_JSON_VERSION,
    RunKind,
    SyncStatus,
)
from exlab_wizard.controller import (
    CreationController,
    NoOpNASSync,
    NoOpReadmeGenerator,
    ProjectCreateRequest,
    RunCreateRequest,
    SessionState,
    SessionStore,
)
from exlab_wizard.template.copier_driver import TemplateEngine
from exlab_wizard.validator.engine import Validator

FIXTURE_TEMPLATES = Path(__file__).parent.parent.parent / "fixtures" / "templates"
FIXTURE_PLUGINS = Path(__file__).parent.parent.parent / "fixtures" / "plugins"

RUN_TEMPLATE = FIXTURE_TEMPLATES / "run_basic_experimental"


# ---------------------------------------------------------------------------
# Helpers (mirror tests/integration/controller/test_creation_flow.py)
# ---------------------------------------------------------------------------


def _build_config(local_root: Path) -> Config:
    # ``local_root`` is ``tmp_path / "data"``; app_root is its parent so the
    # derived ``config.paths.local_root`` resolves back to it. Templates/plugins
    # are decorative (provenance copies the request's explicit template_path).
    return Config(
        paths=PathsConfig(app_root=str(local_root.parent)),
        equipment=[
            EquipmentConfig(
                id="EQ1",
                label="Equipment 1",
                nas_root="/srv/nas",
            )
        ],
        operators=OperatorsConfig(allowlist=[]),
        readme=READMEConfig(defaults=[]),
    )


def _build_controller(config: Config) -> CreationController:
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
        readme_extra={},
    )


def _run_request(run_date: datetime) -> RunCreateRequest:
    return RunCreateRequest(
        equipment_id="EQ1",
        project_name="Cortex Q3 Pilot",
        template_path=RUN_TEMPLATE,
        run_kind=RunKind.EXPERIMENTAL,
        variables={"run_id": "run_001"},
        label="calibration sweep",
        operator="asmith",
        objective="Sweep the laser wavelengths.",
        readme_extra={},
        run_date=run_date,
    )


async def _drain_to_done(controller: CreationController, session_id: str) -> dict[str, Any]:
    task = controller._tasks.get(session_id)
    if task is not None:
        await task
    handle = await controller.status(session_id)
    return {"state": handle.state, "current_phase": handle.current_phase}


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


async def test_run_creation_freezes_template_provenance_copy(tmp_path: Path) -> None:
    """A run's instance dir carries a frozen verbatim copy of its run
    template, creation.json records the relative path, and the copied
    copier.yml/.jinja files do not block sync via post-validate."""
    local_root = tmp_path / "data"
    local_root.mkdir()
    config = _build_config(local_root)
    controller = _build_controller(config)

    # Parent project first so the run inherits a real LIMS block.
    project_handle = await controller.create_project(_project_request())
    project_final = await _drain_to_done(controller, project_handle.session_id)
    assert project_final["state"] is SessionState.DONE

    run_date = datetime(2026, 4, 17, 14, 32, 0, tzinfo=UTC)
    run_handle = await controller.create_run(_run_request(run_date))
    run_final = await _drain_to_done(controller, run_handle.session_id)
    assert run_final["state"] is SessionState.DONE

    run_dir = local_root / "EQ1" / "Cortex Q3 Pilot" / "Runs" / "Run_2026-04-17T14-32"
    assert run_dir.is_dir()

    # The frozen provenance copy is present, verbatim (copier.yml + .jinja).
    template_name = RUN_TEMPLATE.name
    provenance_root = run_dir / CACHE_DIR_NAME / "templates" / "run" / template_name
    assert (provenance_root / "copier.yml").is_file()
    jinja_copy = provenance_root / "run_data.txt.jinja"
    assert jinja_copy.is_file()
    # The .jinja is frozen unrendered (still contains the Jinja placeholder).
    assert "{{ run_id }}" in jinja_copy.read_text(encoding="utf-8")

    # creation.json records the instance-relative provenance path.
    cache_path = run_dir / CACHE_DIR_NAME / CREATION_JSON_NAME
    decoded = msgspec.json.decode(cache_path.read_bytes(), type=CreationJson)
    assert decoded.schema_version == CREATION_JSON_VERSION
    assert decoded.template.provenance_path.startswith(".exlab-wizard/templates/run/")
    assert decoded.template.provenance_path == f".exlab-wizard/templates/run/{template_name}"

    # The copied copier.yml / .jinja are NOT scanned as run output, so the
    # session reached DONE without being blocked by post-validate.
    assert decoded.sync_status != SyncStatus.BLOCKED_BY_VALIDATION.value
    assert decoded.sync_status == SyncStatus.PENDING.value
