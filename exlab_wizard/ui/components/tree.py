"""Project / equipment tree (Frontend Spec §3.5).

Renders the ``<equipment>/<project>/<run>`` hierarchy:

* Equipment node -- equipment ID in heading color.
* Project node -- human name + short_id, with optional archived /
  deleted-from-LIMS treatment.
* Run node (experimental) -- ``Run_<DATE>`` + label.
* Run node (test) -- dimmed styling + ``TestRun_`` prefix in
  warning-tier color + a *"Test"* pill.

``.exlab-wizard/`` folders are hidden by default (Frontend §13.1) and
hidden filtering is the caller's concern.

The component returns a NiceGUI ``ui.tree`` configured with a list of
node dicts; tests can assert on the data shape without spinning up
NiceGUI.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from exlab_wizard.logging import get_logger

_log = get_logger(__name__)


# Node kinds.
KIND_EQUIPMENT = "equipment"
KIND_PROJECT = "project"
KIND_RUN_EXPERIMENTAL = "run_experimental"
KIND_RUN_TEST = "run_test"


# LIMS-side project status.
PROJECT_ACTIVE = "active"
PROJECT_ARCHIVED = "archived"
PROJECT_DELETED = "deleted"


@dataclass(frozen=True)
class TreeFilters:
    """Filter chip state passed to the tree (Frontend §3.5.4)."""

    active: bool = True
    archived: bool = False
    test_runs: bool = True
    search: str = ""


@dataclass(frozen=True)
class EquipmentNode:
    equipment_id: str


@dataclass(frozen=True)
class ProjectNode:
    short_id: str
    name: str
    status: str = PROJECT_ACTIVE


@dataclass(frozen=True)
class RunNode:
    directory_name: str
    run_kind: str  # "experimental" | "test"
    label: str | None = None


@dataclass(frozen=True)
class TreeNode:
    """A renderable tree node (post-filter)."""

    node_id: str
    label: str
    kind: str
    children: tuple[TreeNode, ...] = field(default_factory=tuple)
    badges: tuple[str, ...] = field(default_factory=tuple)
    style_hints: dict[str, str] = field(default_factory=dict)


def _matches_search(text: str, query: str) -> bool:
    """Case-insensitive substring match used by the search box."""

    if not query:
        return True
    return query.lower() in text.lower()


def filter_project(project: ProjectNode, filters: TreeFilters) -> bool:
    """Return ``True`` when ``project`` should be rendered.

    Active default-on; Archived default-off. Deleted-from-LIMS rows
    always render (Frontend §3.5.3).
    """

    if project.status == PROJECT_DELETED:
        return True
    if project.status == PROJECT_ACTIVE and not filters.active:
        return False
    return not (project.status == PROJECT_ARCHIVED and not filters.archived)


def filter_run(run: RunNode, filters: TreeFilters) -> bool:
    """Return ``True`` when ``run`` should be rendered.

    Test runs default-on; toggling the chip off hides them.
    """

    return not (run.run_kind == "test" and not filters.test_runs)


def build_nodes(
    *,
    hierarchy: dict[EquipmentNode, dict[ProjectNode, list[RunNode]]],
    filters: TreeFilters,
) -> list[TreeNode]:
    """Translate a hierarchy into a list of :class:`TreeNode`."""

    nodes: list[TreeNode] = []
    for equipment, projects in hierarchy.items():
        project_nodes: list[TreeNode] = []
        for project, runs in projects.items():
            if not filter_project(project, filters):
                continue
            project_label = f"{project.name}  ·  {project.short_id}"
            project_search = f"{project.name} {project.short_id}"
            run_nodes: list[TreeNode] = []
            for run in runs:
                if not filter_run(run, filters):
                    continue
                if (
                    run.label
                    and not _matches_search(f"{run.directory_name} {run.label}", filters.search)
                    and not _matches_search(project_search, filters.search)
                ):
                    continue
                if (
                    not run.label
                    and not _matches_search(run.directory_name, filters.search)
                    and not _matches_search(project_search, filters.search)
                ):
                    continue
                if run.run_kind == "test":
                    style_hints = {"variant": "dim", "prefix_color": "--color-warning"}
                    badges = ("Test",)
                    kind = KIND_RUN_TEST
                else:
                    style_hints = {"variant": "default"}
                    badges = ()
                    kind = KIND_RUN_EXPERIMENTAL
                run_label = run.directory_name + (f"  --  {run.label}" if run.label else "")
                run_nodes.append(
                    TreeNode(
                        node_id=f"{equipment.equipment_id}/{project.short_id}/{run.directory_name}",
                        label=run_label,
                        kind=kind,
                        badges=badges,
                        style_hints=style_hints,
                    )
                )

            if not run_nodes and not _matches_search(project_search, filters.search):
                continue

            project_style: dict[str, str] = {}
            project_badges: tuple[str, ...] = ()
            if project.status == PROJECT_ARCHIVED:
                project_style["text_decoration"] = "line-through"
                project_badges = ("(archived)",)
            elif project.status == PROJECT_DELETED:
                project_style["text_color"] = "var(--color-warning)"
                project_badges = ("(LIMS project removed)",)

            project_nodes.append(
                TreeNode(
                    node_id=f"{equipment.equipment_id}/{project.short_id}",
                    label=project_label,
                    kind=KIND_PROJECT,
                    children=tuple(run_nodes),
                    badges=project_badges,
                    style_hints=project_style,
                )
            )
        nodes.append(
            TreeNode(
                node_id=equipment.equipment_id,
                label=equipment.equipment_id,
                kind=KIND_EQUIPMENT,
                children=tuple(project_nodes),
            )
        )
    return nodes


def to_nicegui_nodes(nodes: Iterable[TreeNode]) -> list[dict[str, Any]]:
    """Convert :class:`TreeNode` instances to NiceGUI ``ui.tree`` dicts."""

    return [
        {
            "id": node.node_id,
            "label": node.label,
            "kind": node.kind,
            "badges": list(node.badges),
            "children": to_nicegui_nodes(node.children),
        }
        for node in nodes
    ]


def build_tree(
    *,
    hierarchy: dict[EquipmentNode, dict[ProjectNode, list[RunNode]]],
    on_select: Callable[[str], None] | None = None,
    filters: TreeFilters | None = None,
) -> Any:
    """Build the project / equipment tree.

    Returns the NiceGUI ``ui.tree`` element, or the immutable nodes list
    when called outside of a NiceGUI app context (tests).
    """

    f = filters or TreeFilters()
    nodes = build_nodes(hierarchy=hierarchy, filters=f)
    payload = to_nicegui_nodes(nodes)
    try:
        from nicegui import ui
    except Exception:
        return payload

    tree = ui.tree(payload, label_key="label", node_key="id").props('data-testid="main-tree"')
    if on_select is not None:

        def _selected(event: Any) -> None:
            on_select(event.value)

        tree.on_select(_selected)
    return tree
