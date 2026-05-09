"""``/tree`` and ``/run/{path}`` browse endpoints. Backend Spec §4.6.1.

Two endpoints back the Frontend's tree view (Frontend §3.6) and run
detail panel (Frontend §3.6.2):

* ``GET /tree`` -- equipment / project / run hierarchy.
* ``GET /run/{path}`` -- single-run detail (template, operator, sync
  status, run kind, README content).

The router walks the local filesystem under each configured equipment
root via ``os.scandir`` (the same iterator the validator uses for
audit-mode walks; §4.5). ``creation.json`` is decoded via
``msgspec.json.decode`` per §4.4.5.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import msgspec
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict

from exlab_wizard.api.schemas import CreationJson
from exlab_wizard.api.setup import setup_state_gate
from exlab_wizard.constants import (
    CACHE_DIR_NAME,
    README_FILE_NAME,
    TEST_RUNS_DIR_NAME,
)
from exlab_wizard.io import read_msgspec_json
from exlab_wizard.logging import get_logger
from exlab_wizard.paths import creation_json_path, is_run_dir, is_test_run_dir

__all__ = [
    "EquipmentNode",
    "ProjectNode",
    "RunDetail",
    "RunNode",
    "TreeResponse",
    "build_browse_router",
]

_log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class RunNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    path: str
    kind: str  # "experimental" | "test"
    sync_status: str | None = None
    has_creation_json: bool = False


class ProjectNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    short_id: str
    path: str
    runs: list[RunNode] = []
    test_runs: list[RunNode] = []
    has_creation_json: bool = False


class EquipmentNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    path: str
    projects: list[ProjectNode] = []


class TreeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    equipment: list[EquipmentNode]


class RunDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    schema_version: str | None = None
    template: dict[str, Any] | None = None
    operator: str | None = None
    label: str | None = None
    run_kind: str | None = None
    sync_status: str | None = None
    readme: str | None = None
    plugins_applied: list[dict[str, Any]] = []
    validation_overrides: list[dict[str, Any]] = []


# ---------------------------------------------------------------------------
# Router builder
# ---------------------------------------------------------------------------


def build_browse_router() -> APIRouter:
    """Construct the ``/tree`` + ``/run`` router."""
    router = APIRouter(tags=["browse"])

    @router.get(
        "/tree",
        response_model=TreeResponse,
        dependencies=[Depends(setup_state_gate)],
    )
    async def get_tree(request: Request) -> TreeResponse:
        deps = _require_deps(request)
        config = getattr(deps, "config", None)
        if config is None:
            return TreeResponse(equipment=[])
        nodes = [
            _build_equipment_node(entry, Path(config.paths.local_root))
            for entry in config.equipment
        ]
        return TreeResponse(equipment=nodes)

    @router.get(
        "/run/{run_path:path}",
        response_model=RunDetail,
        dependencies=[Depends(setup_state_gate)],
    )
    async def get_run(run_path: str) -> RunDetail:
        path = Path(run_path)
        cache_path = creation_json_path(path)
        if not cache_path.exists():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "session_not_found",
                    "message": f"creation.json not found at {cache_path}",
                },
            )
        try:
            payload = read_msgspec_json(cache_path, CreationJson)
        except (msgspec.DecodeError, msgspec.ValidationError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "validation_failed",
                    "message": str(exc),
                },
            ) from exc
        readme_text = _read_readme(path)
        return RunDetail(
            path=str(path),
            schema_version=payload.schema_version,
            template={
                "name": payload.template.name,
                "version": payload.template.version,
                "source_path": payload.template.source_path,
                "run_scope": payload.template.run_scope,
            },
            operator=payload.created_by,
            label=payload.lims_project.name_at_creation,
            run_kind=payload.run_kind,
            sync_status=payload.sync_status,
            readme=readme_text,
            plugins_applied=[msgspec.to_builtins(applied) for applied in payload.plugins_applied],
            validation_overrides=list(payload.validation_overrides),
        )

    return router


# ---------------------------------------------------------------------------
# Tree builders
# ---------------------------------------------------------------------------


def _build_equipment_node(entry: Any, local_root: Path) -> EquipmentNode:
    equipment_dir = local_root / entry.id
    projects = _scan_projects(equipment_dir) if equipment_dir.exists() else []
    return EquipmentNode(
        id=entry.id,
        label=entry.label or entry.id,
        path=str(equipment_dir),
        projects=projects,
    )


def _scan_projects(equipment_dir: Path) -> list[ProjectNode]:
    """Return a sorted list of project nodes under an equipment dir."""
    nodes: list[ProjectNode] = []
    try:
        entries = list(os.scandir(equipment_dir))
    except (FileNotFoundError, PermissionError):
        return []
    for entry in sorted(entries, key=lambda e: e.name):
        if not entry.is_dir(follow_symlinks=False):
            continue
        if entry.name == CACHE_DIR_NAME:
            continue
        path = Path(entry.path)
        nodes.append(_build_project_node(path))
    return nodes


def _build_project_node(project_dir: Path) -> ProjectNode:
    runs: list[RunNode] = []
    test_runs: list[RunNode] = []
    try:
        entries = list(os.scandir(project_dir))
    except (FileNotFoundError, PermissionError):
        entries = []
    for entry in sorted(entries, key=lambda e: e.name):
        if not entry.is_dir(follow_symlinks=False):
            continue
        if entry.name == CACHE_DIR_NAME:
            continue
        if entry.name == TEST_RUNS_DIR_NAME:
            test_runs.extend(_scan_run_children(Path(entry.path), kind="test"))
            continue
        if is_run_dir(entry.name):
            runs.append(_build_run_node(Path(entry.path), kind="experimental"))
    has_cache = creation_json_path(project_dir).exists()
    return ProjectNode(
        short_id=project_dir.name,
        path=str(project_dir),
        runs=runs,
        test_runs=test_runs,
        has_creation_json=has_cache,
    )


def _scan_run_children(test_runs_dir: Path, *, kind: str) -> list[RunNode]:
    out: list[RunNode] = []
    try:
        entries = list(os.scandir(test_runs_dir))
    except (FileNotFoundError, PermissionError):
        return out
    for entry in sorted(entries, key=lambda e: e.name):
        if not entry.is_dir(follow_symlinks=False):
            continue
        if entry.name == CACHE_DIR_NAME:
            continue
        if not is_test_run_dir(entry.name):
            continue
        out.append(_build_run_node(Path(entry.path), kind=kind))
    return out


def _build_run_node(run_dir: Path, *, kind: str) -> RunNode:
    cache_path = creation_json_path(run_dir)
    sync_status: str | None = None
    has_cache = cache_path.exists()
    if has_cache:
        try:
            payload = read_msgspec_json(cache_path, CreationJson)
            sync_status = payload.sync_status
        except (msgspec.DecodeError, msgspec.ValidationError):
            sync_status = None
    return RunNode(
        name=run_dir.name,
        path=str(run_dir),
        kind=kind,
        sync_status=sync_status,
        has_creation_json=has_cache,
    )


# ---------------------------------------------------------------------------
# Run-detail helpers
# ---------------------------------------------------------------------------


def _read_readme(run_dir: Path) -> str | None:
    """Return the run's README.md text, or ``None`` if absent / unreadable."""
    readme_path = run_dir / README_FILE_NAME
    if not readme_path.exists():
        return None
    try:
        return readme_path.read_text(encoding="utf-8")
    except OSError:
        return None


def _require_deps(request: Request) -> Any:
    deps = getattr(request.app.state, "dependencies", None)
    if deps is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "internal_error",
                "message": "app dependencies are not initialized",
            },
        )
    return deps
