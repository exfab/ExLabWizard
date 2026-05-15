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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import msgspec
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict

from exlab_wizard.api._dependencies import require_deps
from exlab_wizard.api.schemas import CreationJson
from exlab_wizard.api.setup import setup_state_gate
from exlab_wizard.constants import (
    CACHE_DIR_NAME,
    README_FILE_NAME,
    RUNS_DIR_NAME,
    TEST_RUNS_DIR_NAME,
)
from exlab_wizard.io import read_msgspec_json
from exlab_wizard.logging import get_logger
from exlab_wizard.paths import creation_json_path, is_run_dir, is_test_run_dir
from exlab_wizard.utils.time import dt_to_iso

__all__ = [
    "EquipmentNode",
    "FolderEntry",
    "FolderResponse",
    "ProjectNode",
    "RelayEquipmentNode",
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

    # The on-disk project folder name -- the human-readable LIMS project
    # name used verbatim (Backend Spec §3.2). The LIMS short_id is a
    # barcoding identifier kept in the project's creation.json metadata,
    # not in the directory path.
    name: str
    path: str
    runs: list[RunNode] = []
    test_runs: list[RunNode] = []
    has_creation_json: bool = False


class EquipmentNode(BaseModel):
    """An owned-equipment node in the tree (Redesign §3.3).

    ``sync_mode`` ("nas" | "stage") drives the per-equipment badge.
    ``relay`` is False for owned equipment; the received-equipment node
    type has its own ``RelayEquipmentNode`` shape below.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    path: str
    sync_mode: str = "nas"
    relay: bool = False
    projects: list[ProjectNode] = []


class RelayEquipmentNode(BaseModel):
    """A received-equipment node auto-discovered from the staging area.

    Redesign §3.3: the orchestrator surfaces equipment it has received
    runs for, even though they are not in its local config registry.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    path: str
    relay: bool = True
    projects: list[ProjectNode] = []


class TreeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    equipment: list[EquipmentNode]
    received_equipment: list[RelayEquipmentNode] = []


class FolderEntry(BaseModel):
    """One row in the new ``GET /folder/{path}`` response. Redesign §4.3 / §5."""

    model_config = ConfigDict(extra="forbid")

    name: str
    path: str
    is_dir: bool
    size_bytes: int | None = None
    modified_iso: str | None = None
    sync_status: str | None = None


class FolderResponse(BaseModel):
    """Immediate contents of one folder. Redesign §5 (live file feed)."""

    model_config = ConfigDict(extra="forbid")

    path: str
    entries: list[FolderEntry]


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
        deps = require_deps(request)
        config = getattr(deps, "config", None)
        if config is None:
            return TreeResponse(equipment=[])
        nodes = [
            _build_equipment_node(entry, Path(config.paths.local_root))
            for entry in config.equipment
        ]
        received = _build_received_equipment_nodes(config)
        return TreeResponse(equipment=nodes, received_equipment=received)

    @router.get(
        "/folder/{folder_path:path}",
        response_model=FolderResponse,
        dependencies=[Depends(setup_state_gate)],
    )
    async def get_folder(request: Request, folder_path: str) -> FolderResponse:
        """Return the immediate contents of one folder.

        Redesign §5: drives the centre-pane live file feed; 2-3s poll
        from the UI. Returns 404 when the folder no longer exists,
        403 when the path falls outside the configured roots
        (local_root / staging_root / templates / plugins).
        """
        deps = require_deps(request)
        config = getattr(deps, "config", None)
        try:
            path = Path(folder_path).resolve(strict=True)
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "folder_not_found",
                    "message": f"folder does not exist: {folder_path}",
                },
            ) from exc
        if not _path_is_under_allowed_root(path, config):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "permission_denied",
                    "message": (
                        f"path {path} is outside the configured local_root "
                        "/ staging_root / templates / plugins roots"
                    ),
                },
            )
        if not path.is_dir():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "folder_not_found",
                    "message": f"folder does not exist: {path}",
                },
            )
        entries: list[FolderEntry] = []
        try:
            scandir_entries = list(os.scandir(path))
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "folder_not_found",
                    "message": f"folder vanished during scan: {path}",
                },
            ) from exc
        except (PermissionError, OSError) as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "permission_denied",
                    "message": f"cannot list {path}: {exc}",
                },
            ) from exc
        for entry in sorted(scandir_entries, key=lambda e: e.name):
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
                stat = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            entries.append(
                FolderEntry(
                    name=entry.name,
                    path=entry.path,
                    is_dir=is_dir,
                    size_bytes=None if is_dir else stat.st_size,
                    modified_iso=dt_to_iso(datetime.fromtimestamp(stat.st_mtime, tz=UTC)),
                    sync_status=_per_file_sync_status(Path(entry.path)),
                )
            )
        return FolderResponse(path=str(path), entries=entries)

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


def _path_is_under_allowed_root(path: Path, config: Any) -> bool:
    """Return True if ``path`` is under any of the configured roots.

    Redesign §5 / §10: GET /folder is sandboxed to the configured
    local_root, staging_root, templates_dir, and plugin_dir to prevent
    arbitrary host-path enumeration via the API. ``path`` must be the
    already-resolved absolute form (no symlink-escape via ``..``).
    """
    if config is None:
        return False
    candidates: list[str] = []
    paths_block = getattr(config, "paths", None)
    if paths_block is not None:
        for attr in ("local_root", "templates_dir", "plugin_dir"):
            val = getattr(paths_block, attr, "")
            if val:
                candidates.append(val)
    orch = getattr(config, "orchestrator", None)
    if orch is not None:
        val = getattr(orch, "staging_root", "")
        if val:
            candidates.append(val)
    for root in candidates:
        try:
            resolved_root = Path(root).resolve()
        except OSError:
            continue
        try:
            path.relative_to(resolved_root)
            return True
        except ValueError:
            continue
    return False


def _build_equipment_node(entry: Any, local_root: Path) -> EquipmentNode:
    equipment_dir = local_root / entry.id
    projects = _scan_projects(equipment_dir) if equipment_dir.exists() else []
    sync_mode = (
        entry.sync_mode.value
        if hasattr(entry.sync_mode, "value")
        else str(entry.sync_mode)
    )
    return EquipmentNode(
        id=entry.id,
        label=entry.label or entry.id,
        path=str(equipment_dir),
        sync_mode=sync_mode,
        relay=False,
        projects=projects,
    )


def _build_received_equipment_nodes(config: Any) -> list[RelayEquipmentNode]:
    """Walk the staging root and surface received-equipment nodes.

    Redesign §3.3: auto-discovered from the runs the orchestrator has
    received; the equipment is NOT in this device's config registry.
    """
    staging_root_str = getattr(config.orchestrator, "staging_root", "")
    if not staging_root_str:
        return []
    staging_root = Path(staging_root_str)
    if not staging_root.exists():
        return []
    # Each immediate subdirectory of staging_root is an equipment id.
    out: list[RelayEquipmentNode] = []
    owned_ids = {entry.id for entry in config.equipment}
    try:
        children = sorted(os.scandir(staging_root), key=lambda e: e.name)
    except OSError:
        return []
    for child in children:
        if not child.is_dir(follow_symlinks=False) or child.name == CACHE_DIR_NAME:
            continue
        if child.name in owned_ids:
            # Owned equipment also surfaces in the staging root when the
            # orchestrator and acquisition coexist on the same device;
            # skip the relay node for it.
            continue
        equipment_dir = Path(child.path)
        projects = _scan_projects(equipment_dir)
        if not projects:
            continue
        out.append(
            RelayEquipmentNode(
                id=child.name,
                label=_relay_label_from_first_run(equipment_dir, child.name),
                path=str(equipment_dir),
                relay=True,
                projects=projects,
            )
        )
    return out


def _relay_label_from_first_run(equipment_dir: Path, fallback: str) -> str:
    """Best-effort: read the first creation.json under this equipment to
    pick up the relay ``equipment_label`` field set by the producer."""
    for project_dir in _iter_run_or_project_subdirs(equipment_dir):
        for kind_dir in _iter_run_or_project_subdirs(Path(project_dir.path)):
            if kind_dir.name not in (RUNS_DIR_NAME, TEST_RUNS_DIR_NAME):
                continue
            for leaf in _iter_run_or_project_subdirs(Path(kind_dir.path)):
                try:
                    cache = creation_json_path(Path(leaf.path))
                    if not cache.exists():
                        continue
                    payload = read_msgspec_json(cache, CreationJson)
                except (msgspec.DecodeError, msgspec.ValidationError, OSError):
                    continue
                if payload.orchestrator and payload.orchestrator.equipment_label:
                    return payload.orchestrator.equipment_label
                return fallback
    return fallback


def _per_file_sync_status(path: Path) -> str | None:
    """Return per-file sync status for a folder-list row.

    Redesign §5 last bullet:
    - For files under owned ``nas`` equipment: derived from the run's
      ``creation.json`` ``sync_status`` (pending → synced → verified).
    - For files under owned ``stage`` equipment: tops out at ``relayed``.
    - For files under received equipment: derived from ``ingest.json``
      lifecycle state.

    Implemented conservatively: walks up to find the nearest
    ``creation.json`` (run cache) and returns its ``sync_status`` if
    present. Receives a ``None`` when nothing applies.
    """
    if path.is_dir():
        return None
    # Walk up to find a Run_*/.exlab-wizard/creation.json. The bound (10)
    # tolerates typical instrument output tree depths (Run/data/raw/
    # series/frames/...) without becoming pathological for misrooted paths.
    current = path.parent
    for _ in range(10):
        try:
            cache_path = creation_json_path(current)
            if cache_path.exists():
                payload = read_msgspec_json(cache_path, CreationJson)
                return payload.sync_status
        except (msgspec.DecodeError, msgspec.ValidationError, OSError):
            return None
        if current.parent == current:
            break
        current = current.parent
    return None


def _iter_run_or_project_subdirs(parent: Path) -> list[os.DirEntry[str]]:
    """Return name-sorted real subdirectories of ``parent``, sans the cache.

    Hides the ``.exlab-wizard/`` cache directory and silently swallows
    ``FileNotFoundError`` / ``PermissionError`` so callers can iterate
    without per-call ``try``/``except`` blocks. The returned list is
    sorted lexicographically by entry name so the on-wire tree order is
    stable across runs.
    """
    try:
        entries = list(os.scandir(parent))
    except (FileNotFoundError, PermissionError):
        return []
    out: list[os.DirEntry[str]] = []
    for entry in sorted(entries, key=lambda e: e.name):
        if not entry.is_dir(follow_symlinks=False):
            continue
        if entry.name == CACHE_DIR_NAME:
            continue
        out.append(entry)
    return out


def _scan_projects(equipment_dir: Path) -> list[ProjectNode]:
    """Return a sorted list of project nodes under an equipment dir."""
    return [
        _build_project_node(Path(entry.path))
        for entry in _iter_run_or_project_subdirs(equipment_dir)
    ]


def _build_project_node(project_dir: Path) -> ProjectNode:
    runs: list[RunNode] = []
    test_runs: list[RunNode] = []
    for entry in _iter_run_or_project_subdirs(project_dir):
        if entry.name == TEST_RUNS_DIR_NAME:
            test_runs.extend(
                _scan_run_children(Path(entry.path), kind="test", prefix_check=is_test_run_dir)
            )
            continue
        if entry.name == RUNS_DIR_NAME:
            # Redesign §3.4: experimental runs live under <project>/Runs/.
            runs.extend(
                _scan_run_children(
                    Path(entry.path), kind="experimental", prefix_check=is_run_dir
                )
            )
            continue
        if is_run_dir(entry.name):
            # Misplaced Run_* directly under the project (pre-redesign
            # layout); still surfaced so the validator flags it.
            runs.append(_build_run_node(Path(entry.path), kind="experimental"))
    has_cache = creation_json_path(project_dir).exists()
    return ProjectNode(
        name=project_dir.name,
        path=str(project_dir),
        runs=runs,
        test_runs=test_runs,
        has_creation_json=has_cache,
    )


def _scan_run_children(
    marker_dir: Path,
    *,
    kind: str,
    prefix_check: Any = None,
) -> list[RunNode]:
    check = prefix_check or is_test_run_dir
    return [
        _build_run_node(Path(entry.path), kind=kind)
        for entry in _iter_run_or_project_subdirs(marker_dir)
        if check(entry.name)
    ]


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
