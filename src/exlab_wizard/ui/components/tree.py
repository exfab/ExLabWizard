"""Project / equipment tree (Frontend Spec §3.5).

Renders the ``<equipment>/<project>/<run>`` hierarchy:

* Equipment node -- equipment ID in heading color.
* Project node -- human name + short_id, with optional archived /
  deleted-from-LIMS treatment.
* Run node (experimental) -- ``Run_<DATE>`` + label.
* Run node (test) -- dimmed styling + ``TestRun_`` prefix in
  warning-tier color + a *"Test"* pill.

Run rows also carry a small **sync icon** to the left of the label:

* ``sync_local.svg`` -- run data is still on local disk (any sync
  status other than ``cleaned``).
* ``sync_cloud.svg`` -- run has been synced, verified, and locally
  cleaned (``sync_status == "cleaned"``); only the ``.exlab-wizard/``
  cache subtree remains on disk (§7.1.10).

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

from exlab_wizard.constants.enums import RunKind, SyncStatus, TreeProjectStatus
from exlab_wizard.logging import get_logger

_log = get_logger(__name__)


# Node kinds.
KIND_EQUIPMENT = "equipment"
KIND_RECEIVED_EQUIPMENT = "received_equipment"
KIND_PROJECT = "project"
KIND_RUN_EXPERIMENTAL = "run_experimental"
KIND_RUN_TEST = "run_test"

_RUN_KINDS: frozenset[str] = frozenset({KIND_RUN_EXPERIMENTAL, KIND_RUN_TEST})
_EQUIPMENT_KINDS: frozenset[str] = frozenset({KIND_EQUIPMENT, KIND_RECEIVED_EQUIPMENT})

# Map internal kind to the testid suffix the Playwright flows expect.
# Both run_experimental and run_test collapse to "run" (the e2e contract
# treats them interchangeably for selection / context-menu purposes).
_TESTID_KIND_BY_KIND: dict[str, str] = {
    KIND_EQUIPMENT: "equipment",
    KIND_RECEIVED_EQUIPMENT: "received_equipment",
    KIND_PROJECT: "project",
    KIND_RUN_EXPERIMENTAL: "run",
    KIND_RUN_TEST: "run",
}

# Static URLs served by ``ui/theme.py:register_static_assets``.
SYNC_ICON_LOCAL_URL = "/assets/sync_local.svg"
SYNC_ICON_CLOUD_URL = "/assets/sync_cloud.svg"


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
    # Redesign §3.3: relay equipment (received from another workstation)
    # renders the same row but disables creation actions and uses a
    # different context-menu surface (none, per decision 3).
    relay: bool = False


@dataclass(frozen=True)
class ProjectNode:
    short_id: str
    name: str
    status: TreeProjectStatus = TreeProjectStatus.ACTIVE


@dataclass(frozen=True)
class RunNode:
    directory_name: str
    run_kind: RunKind
    label: str | None = None
    sync_status: str | None = None  # one of SyncStatus values; None when unknown


@dataclass(frozen=True)
class TreeNode:
    """A renderable tree node (post-filter)."""

    node_id: str
    label: str
    kind: str
    children: tuple[TreeNode, ...] = field(default_factory=tuple)
    badges: tuple[str, ...] = field(default_factory=tuple)
    style_hints: dict[str, str] = field(default_factory=dict)
    sync_status: str | None = None  # set on run nodes only


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

    if project.status == TreeProjectStatus.DELETED:
        return True
    if project.status == TreeProjectStatus.ACTIVE and not filters.active:
        return False
    return not (project.status == TreeProjectStatus.ARCHIVED and not filters.archived)


def filter_run(run: RunNode, filters: TreeFilters) -> bool:
    """Return ``True`` when ``run`` should be rendered.

    Test runs default-on; toggling the chip off hides them.
    """

    return not (run.run_kind == RunKind.TEST and not filters.test_runs)


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
                badges: tuple[str, ...]
                if run.run_kind == RunKind.TEST:
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
                        # node_id mirrors the on-disk path: the project
                        # segment is the human-readable name (§3.2).
                        node_id=f"{equipment.equipment_id}/{project.name}/{run.directory_name}",
                        label=run_label,
                        kind=kind,
                        badges=badges,
                        style_hints=style_hints,
                        sync_status=run.sync_status,
                    )
                )

            if not run_nodes and not _matches_search(project_search, filters.search):
                continue

            project_style: dict[str, str] = {}
            project_badges: tuple[str, ...] = ()
            if project.status == TreeProjectStatus.ARCHIVED:
                project_style["text_decoration"] = "line-through"
                project_badges = ("(archived)",)
            elif project.status == TreeProjectStatus.DELETED:
                project_style["text_color"] = "var(--color-warning)"
                project_badges = ("(LIMS project removed)",)

            project_nodes.append(
                TreeNode(
                    node_id=f"{equipment.equipment_id}/{project.name}",
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
                kind=KIND_RECEIVED_EQUIPMENT if equipment.relay else KIND_EQUIPMENT,
                children=tuple(project_nodes),
            )
        )
    return nodes


def _sync_icon_url(node: TreeNode) -> str | None:
    """Return the per-row sync-icon URL, or ``None`` for non-run rows.

    Run rows get one of the two ``/assets/sync_*.svg`` URLs depending on
    whether the run has been locally cleaned (``sync_cloud.svg``) or
    still has data on disk (``sync_local.svg``). Equipment / project
    rows render unchanged.
    """
    if node.kind not in _RUN_KINDS:
        return None
    if node.sync_status == SyncStatus.CLEANED.value:
        return SYNC_ICON_CLOUD_URL
    return SYNC_ICON_LOCAL_URL


def to_nicegui_nodes(nodes: Iterable[TreeNode]) -> list[dict[str, Any]]:
    """Convert :class:`TreeNode` instances to NiceGUI ``ui.tree`` dicts.

    Run rows additionally carry a ``sync_icon`` URL string and a
    ``sync_status`` string used by the ``default-header`` scoped-slot
    template attached in :func:`build_tree`.

    Each row also carries a ``testid_kind`` field — the suffix the
    Playwright flows expect on ``data-testid="tree-node-<suffix>"``.
    Both run kinds collapse to ``"run"`` so the e2e selectors can
    treat experimental and test runs interchangeably.
    """

    out: list[dict[str, Any]] = []
    for node in nodes:
        payload: dict[str, Any] = {
            "id": node.node_id,
            "label": node.label,
            "kind": node.kind,
            "testid_kind": _TESTID_KIND_BY_KIND.get(node.kind, node.kind),
            "badges": list(node.badges),
            "children": to_nicegui_nodes(node.children),
        }
        icon_url = _sync_icon_url(node)
        if icon_url is not None:
            payload["sync_icon"] = icon_url
            payload["sync_status"] = node.sync_status or ""
        out.append(payload)
    return out


# Quasar ``q-tree`` does not honour an ``icon`` / ``img`` field on plain
# node dicts; per-node images must come through a scoped slot template.
# The ``default-header`` template renders the per-row sync icon (when
# present) and emits the data-testid / data-node-id attributes the
# Playwright flows assert on. Right-click context menus are NOT inlined
# in the slot because Quasar's ``q-menu`` mounts in a body-level
# Teleport portal, which breaks both DOM-event bubbling back to the
# tree wrapper and Vue ``$emit`` forwarding through scoped-slot scope.
# Instead :func:`build_tree` renders one Python-side ``ui.menu()`` per
# context-eligible node after the tree mounts, anchoring each menu to
# its row via Quasar's ``target`` selector pointing at the row's
# unique ``[data-node-id="..."]``. Menu items are plain ``ui.menu_item``
# calls whose ``on_click`` lambdas run server-side, so no JS bridging
# is needed.
_TREE_DEFAULT_HEADER_SLOT = (
    '<div class="row items-center" style="gap: 0.4rem">'
    '<img v-if="props.node.sync_icon" :src="props.node.sync_icon" '
    'style="width: 1rem; height: 1rem; flex-shrink: 0;" '
    ":alt=\"props.node.sync_status || ''\" />"
    "<span :data-testid=\"'tree-node-' + props.node.testid_kind\" "
    ':data-node-id="props.node.id" '
    ':data-kind="props.node.kind" '
    ":data-sync-status=\"props.node.sync_status || ''\">"
    "{{ props.node.label }}"
    "</span>"
    "</div>"
)


def _iter_nodes(nodes: Iterable[TreeNode]) -> Iterable[TreeNode]:
    """Yield every node in ``nodes``, depth-first, including children."""
    for node in nodes:
        yield node
        yield from _iter_nodes(node.children)


def _selector_for_node_id(node_id: str) -> str:
    """Build the Quasar ``target`` selector for a node row.

    ``node_id`` is the on-disk path segment used in the tree (e.g.
    ``EQ1/PROJ-0001/Run_2026-05-07``); the slot template emits it on
    the row's ``data-node-id`` attribute. CSS attribute selectors
    require backslash-escaping for any ``\\``, ``"``, ``/`` is safe.
    """
    escaped = node_id.replace("\\", "\\\\").replace('"', '\\"')
    return f'[data-node-id="{escaped}"]'


def _render_node_context_menus(
    nodes: Iterable[TreeNode],
    *,
    on_equipment_context_action: Callable[[str, str], None] | None,
    on_run_context_action: Callable[[str, str], None] | None,
) -> None:  # pragma: no cover -- NiceGUI render, driven by e2e
    """Render per-node Quasar context menus anchored by ``data-node-id``.

    Iterates every owned-equipment / run node (received_equipment
    deliberately has no menu per Redesign §3.3 decision 3) and emits a
    ``ui.menu`` configured as a context menu targeting that node's
    row. The menu items' ``on_click`` lambdas route to the
    operator-supplied callbacks server-side; no JavaScript event
    bridging is involved.
    """
    try:
        from nicegui import ui
    except Exception:
        return

    for node in _iter_nodes(nodes):
        if node.kind == KIND_EQUIPMENT and on_equipment_context_action is not None:
            target = _selector_for_node_id(node.node_id)
            with ui.menu().props(
                f'context-menu touch-position auto-close target="{target}"'
            ).props(f'data-testid="tree-context-menu" data-node-id="{node.node_id}"'):
                ui.menu_item(
                    "Edit equipment…",
                    on_click=lambda _evt, nid=node.node_id: on_equipment_context_action(
                        nid, "edit_equipment"
                    ),
                ).props('data-testid="tree-context-edit-equipment"')
                ui.menu_item(
                    "Remove…",
                    on_click=lambda _evt, nid=node.node_id: on_equipment_context_action(
                        nid, "remove_equipment"
                    ),
                ).props('data-testid="tree-context-remove-equipment"')
        elif node.kind in _RUN_KINDS and on_run_context_action is not None:
            target = _selector_for_node_id(node.node_id)
            with ui.menu().props(
                f'context-menu touch-position auto-close target="{target}"'
            ).props(f'data-testid="run-context-menu" data-run-path="{node.node_id}"'):
                ui.menu_item(
                    "Force sync",
                    on_click=lambda _evt, p=node.node_id: on_run_context_action(p, "force_sync"),
                ).props('data-testid="run-context-force-sync"')
                ui.menu_item(
                    "Clear verified",
                    on_click=lambda _evt, p=node.node_id: on_run_context_action(
                        p, "clear_verified"
                    ),
                ).props('data-testid="run-context-clear-verified"')
                ui.menu_item(
                    "View log",
                    on_click=lambda _evt, p=node.node_id: on_run_context_action(p, "view_log"),
                ).props('data-testid="run-context-view-log"')


def build_tree(
    *,
    hierarchy: dict[EquipmentNode, dict[ProjectNode, list[RunNode]]],
    on_select: Callable[[str], None] | None = None,
    on_equipment_context_action: Callable[[str, str], None] | None = None,
    on_run_context_action: Callable[[str, str], None] | None = None,
    filters: TreeFilters | None = None,
    expand_all: bool = False,
) -> Any:
    """Build the project / equipment tree.

    Returns the NiceGUI ``ui.tree`` element, or the immutable nodes list
    when called outside of a NiceGUI app context (tests).

    ``expand_all`` toggles Quasar's ``default-expand-all`` prop -- used
    by e2e tests that need every node visible in the DOM without
    having to click expand carets.

    ``on_equipment_context_action`` and ``on_run_context_action``
    receive ``(node_id, action)`` when the operator picks an item from
    the per-row right-click menu (Redesign §4.6, dec. 4A). Action
    strings match the constants in
    :mod:`exlab_wizard.ui.components.tree_context_menu`
    (``edit_equipment`` / ``remove_equipment`` /
    ``force_sync`` / ``clear_verified`` / ``view_log``). Received-
    equipment rows never raise these callbacks (no context menu).
    """

    f = filters or TreeFilters()
    nodes = build_nodes(hierarchy=hierarchy, filters=f)
    payload = to_nicegui_nodes(nodes)
    try:
        from nicegui import ui
    except Exception:
        return payload

    tree = ui.tree(payload, label_key="label", node_key="id").props('data-testid="main-tree"')
    tree.add_slot("default-header", _TREE_DEFAULT_HEADER_SLOT)
    if expand_all:
        # NiceGUI's wrapper for Quasar's expandAll() method.
        tree.expand()
    if on_select is not None:

        def _selected(event: Any) -> None:
            on_select(event.value)

        tree.on_select(_selected)
    if on_equipment_context_action is not None or on_run_context_action is not None:
        _render_node_context_menus(
            nodes,
            on_equipment_context_action=on_equipment_context_action,
            on_run_context_action=on_run_context_action,
        )
    return tree
