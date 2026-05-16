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
from collections.abc import Callable
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
    "RunLogEntry",
    "RunLogResponse",
    "RunNode",
    "TreeResponse",
    "build_browse_router",
    "build_hierarchy_dict",
    "scan_folder_sync",
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


class RunLogEntry(BaseModel):
    """One row in the ``GET /run/{path}/log`` response.

    The orchestrator does not write per-run log files; the "log" for a
    run is the state-transition history of its ``ingest.json``. Each
    history entry carries at minimum ``state`` and ``at``; transient
    extras (``host``, ``files_received`` on ``complete``, etc.) are
    forwarded as a free-form payload so the UI can render whatever the
    orchestrator recorded.
    """

    model_config = ConfigDict(extra="forbid")

    state: str
    at: str | None = None
    host: str | None = None
    payload: dict[str, Any] = {}


class RunLogResponse(BaseModel):
    """``GET /run/{path}/log`` response (Redesign §4.6 View-log surface)."""

    model_config = ConfigDict(extra="forbid")

    path: str
    current_state: str | None = None
    history: list[RunLogEntry]


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
        return scan_folder_sync(folder_path, config)

    @router.get(
        "/run/{run_path:path}/log",
        response_model=RunLogResponse,
        dependencies=[Depends(setup_state_gate)],
    )
    async def get_run_log(run_path: str) -> RunLogResponse:
        """Return the staged run's ``ingest.json`` history as a log.

        Redesign §4.6 View-log surface. The orchestrator does not write
        per-run log files; the lifecycle history in
        ``<run>/.exlab-wizard/ingest.json`` IS the per-run log. Returns
        404 when ingest.json doesn't exist (the run hasn't been staged
        yet or has been cleared) and 422 on a parse failure. The
        ``current_state`` field mirrors the most recent history entry
        so the UI can show a header before iterating.

        Declared above the ``GET /run/{run_path:path}`` matcher because
        FastAPI matches routes in declaration order and the ``:path``
        converter would otherwise swallow the trailing ``/log``.
        """
        path = Path(run_path)
        from exlab_wizard.api.schemas import IngestJson as _IngestJson
        from exlab_wizard.constants import INGEST_JSON_NAME as _INGEST_JSON_NAME

        ingest_path = path / CACHE_DIR_NAME / _INGEST_JSON_NAME
        if not ingest_path.exists():
            # Reuse ``session_not_found`` (same allowlist as the run-
            # detail endpoint at GET /run/{path}) rather than minting a
            # new code; semantically the run record is missing in both
            # cases (creation.json there, ingest.json here).
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "session_not_found",
                    "message": f"ingest.json not found at {ingest_path}",
                },
            )
        try:
            payload = read_msgspec_json(ingest_path, _IngestJson)
        except (msgspec.DecodeError, msgspec.ValidationError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "validation_failed",
                    "message": str(exc),
                },
            ) from exc
        history: list[RunLogEntry] = []
        for raw in payload.history:
            state = str(raw.get("state", "")) if isinstance(raw, dict) else ""
            if not state:
                continue
            extras = {
                k: v for k, v in raw.items() if k not in {"state", "at", "host"}
            } if isinstance(raw, dict) else {}
            history.append(
                RunLogEntry(
                    state=state,
                    at=raw.get("at") if isinstance(raw, dict) else None,
                    host=raw.get("host") if isinstance(raw, dict) else None,
                    payload=extras,
                )
            )
        return RunLogResponse(
            path=str(path),
            current_state=str(payload.current_state) if payload.current_state else None,
            history=history,
        )

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
    paths_block = getattr(config, "paths", None)
    orch = getattr(config, "orchestrator", None)
    candidates = (
        [getattr(paths_block, attr, "") for attr in ("local_root", "templates_dir", "plugin_dir")]
        if paths_block is not None
        else []
    )
    if orch is not None:
        candidates.append(getattr(orch, "staging_root", ""))
    for root in candidates:
        if not root:
            continue
        try:
            if path.is_relative_to(Path(root).resolve()):
                return True
        except OSError:
            continue
    return False


def _build_equipment_node(entry: Any, local_root: Path) -> EquipmentNode:
    equipment_dir = local_root / entry.id
    projects = _scan_projects(equipment_dir) if equipment_dir.exists() else []
    # ``entry.sync_mode`` is a :class:`SyncMode` StrEnum; ``str(...)`` returns
    # the bare value (``"nas"`` / ``"stage"``).
    return EquipmentNode(
        id=entry.id,
        label=entry.label or entry.id,
        path=str(equipment_dir),
        sync_mode=str(entry.sync_mode),
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
                _scan_run_children(Path(entry.path), kind="experimental", prefix_check=is_run_dir)
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
    prefix_check: Callable[[str], bool],
) -> list[RunNode]:
    return [
        _build_run_node(Path(entry.path), kind=kind)
        for entry in _iter_run_or_project_subdirs(marker_dir)
        if prefix_check(entry.name)
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


# ---------------------------------------------------------------------------
# Public helpers consumed by the NiceGUI mount
# ---------------------------------------------------------------------------


def scan_folder_sync(folder_path: str, config: Any) -> FolderResponse:
    """Synchronous core of the ``GET /folder/{path}`` endpoint.

    Extracted so the NiceGUI mount can drive the same scan from a
    thread via :func:`asyncio.to_thread` (matching the convention in
    :mod:`exlab_wizard.cache.equipment`). Raises the same FastAPI
    :class:`HTTPException` instances as the endpoint so the HTTP
    response code is preserved when called from a router; the
    NiceGUI mount catches them and renders the appropriate UI state.
    """
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


def build_hierarchy_dict(config: Any) -> dict[Any, dict[Any, list[Any]]]:
    """Compose the nested hierarchy dict that ``ui.components.tree.build_tree`` expects.

    The ``GET /tree`` response is a flat shape (lists of equipment and
    received-equipment models); ``build_tree`` consumes a nested
    ``dict[EquipmentNode, dict[ProjectNode, list[RunNode]]]`` keyed by
    :class:`exlab_wizard.ui.components.tree.EquipmentNode` /
    ``ProjectNode`` / ``RunNode``. This helper bridges the two so the
    NiceGUI mount doesn't have to re-implement the walk.

    Returns an empty dict when ``config`` is ``None``.
    """
    from exlab_wizard.ui.components import tree as ui_tree

    if config is None:
        return {}
    hierarchy: dict[Any, dict[Any, list[Any]]] = {}
    local_root = Path(config.paths.local_root) if config.paths.local_root else Path()
    for entry in config.equipment:
        api_equipment = _build_equipment_node(entry, local_root)
        ui_equipment = ui_tree.EquipmentNode(
            equipment_id=api_equipment.id,
            relay=False,
        )
        hierarchy[ui_equipment] = _projects_for_ui_tree(api_equipment.projects)
    for api_relay in _build_received_equipment_nodes(config):
        ui_equipment = ui_tree.EquipmentNode(
            equipment_id=api_relay.id,
            relay=True,
        )
        hierarchy[ui_equipment] = _projects_for_ui_tree(api_relay.projects)
    return hierarchy


def _projects_for_ui_tree(api_projects: list[ProjectNode]) -> dict[Any, list[Any]]:
    from exlab_wizard.constants import RunKind
    from exlab_wizard.ui.components import tree as ui_tree

    out: dict[Any, list[Any]] = {}
    for api_project in api_projects:
        ui_project = ui_tree.ProjectNode(
            short_id=api_project.name,
            name=api_project.name,
        )
        runs: list[Any] = []
        for api_run in api_project.runs:
            runs.append(
                ui_tree.RunNode(
                    directory_name=api_run.name,
                    run_kind=RunKind.EXPERIMENTAL,
                    sync_status=api_run.sync_status,
                )
            )
        for api_test in api_project.test_runs:
            runs.append(
                ui_tree.RunNode(
                    directory_name=api_test.name,
                    run_kind=RunKind.TEST,
                    sync_status=api_test.sync_status,
                )
            )
        out[ui_project] = runs
    return out
