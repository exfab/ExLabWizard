"""Project / equipment tree (Frontend Spec §3.5).

Renders the ``<equipment>/<project>/<run>`` hierarchy:

* Equipment node -- equipment ID in heading color.
* Project node -- human name + short_id, with optional archived /
  deleted-from-LIMS treatment.
* Run node (experimental) -- ``Run_<DATE>`` + label.
* Run node (test) -- dimmed styling + ``TestRun_`` prefix in
  warning-tier color + a *"Test"* pill.

Run rows also carry a small **sync icon** to the left of the label:

* ``sync_local.svg`` -- run data is still on local disk (rollup
  ``syncing`` / ``synced``, any state other than ``cleared``).
* ``sync_cloud.svg`` -- the run's staging copy has been cleared
  (rollup ``cleared``); only the ``.exlab-wizard/`` cache subtree
  remains on disk (§7.1.10).

The run-node rollup is derived from the run's ``sync_state.json`` by the
browse router (operator-free per-file NAS sync design, 2026-05-21);
``RunNode.sync_status`` carries a :class:`RunSyncState` value.

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

from exlab_wizard.constants.enums import RunKind, RunSyncState, TreeProjectStatus
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


# Node-kind -> (MDI glyph, Okabe-Ito colour token). Distinct from the
# file-type map (different domain): equipment/project/run, not file extensions.
_NODE_TYPE_PROPS: dict[str, tuple[str, str]] = {
    KIND_EQUIPMENT: ("mdi-microscope", "--oi-blue"),
    KIND_RECEIVED_EQUIPMENT: ("mdi-microscope", "--oi-blue"),
    KIND_PROJECT: ("mdi-folder", "--oi-grey"),
    KIND_RUN_EXPERIMENTAL: ("mdi-file-document", "--oi-sky"),
    KIND_RUN_TEST: ("mdi-flask-outline", "--oi-purple"),
}


def _node_type_props(kind: str) -> tuple[str, str]:
    """Return the ``(icon, colour_var)`` for a tree node kind."""
    return _NODE_TYPE_PROPS.get(kind, ("mdi-folder-outline", "--oi-grey"))


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
    the derived run rollup: a ``cleared`` run (staging copy cleaned, data
    on NAS only) gets ``sync_cloud.svg``; a ``syncing`` / ``synced`` run
    still has data on disk and gets ``sync_local.svg``. Equipment /
    project rows render unchanged.
    """
    if node.kind not in _RUN_KINDS:
        return None
    if node.sync_status == RunSyncState.CLEARED.value:
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
        type_icon, type_color = _node_type_props(node.kind)
        payload: dict[str, Any] = {
            "id": node.node_id,
            "label": node.label,
            "kind": node.kind,
            "testid_kind": _TESTID_KIND_BY_KIND.get(node.kind, node.kind),
            "badges": list(node.badges),
            "children": to_nicegui_nodes(node.children),
            "type_icon": type_icon,
            "type_color": type_color,
        }
        icon_url = _sync_icon_url(node)
        if icon_url is not None:
            payload["sync_icon"] = icon_url
            payload["sync_status"] = node.sync_status or ""
            # Friendly hover tooltip mirroring the icon's meaning: the cloud
            # (CLEARED) reads "on NAS only", the local-disk icon reads
            # "data on local disk" (Phase 5 sync tooltips).
            payload["sync_title"] = (
                "Cleared -- data on NAS only"
                if node.sync_status == RunSyncState.CLEARED.value
                else "Data on local disk"
            )
        out.append(payload)
    return out


# Quasar ``q-tree`` does not honour an ``icon`` / ``img`` field on plain
# node dicts; per-node images must come through a scoped slot template.
# The ``default-header`` template renders the per-row sync icon (when
# present), emits the data-testid / data-node-id attributes the
# Playwright flows assert on, and inlines the right-click context menus
# (owned equipment / runs) as Vue ``q-menu`` children. Each q-menu
# auto-attaches to its parent row (no ``target`` selector required, so
# we sidestep both NiceGUI's props-parser quote handling and Quasar's
# mount-before-DOM-ready target-resolution race). Menu items emit the
# concrete NiceGUI websocket event payload for the tree listener. A
# native DOM CustomEvent is not enough here because NiceGUI's
# ``element.on`` registers Vue component listeners, and q-menu content
# is teleported outside the tree row.


def _ctx_emit(*, tree_id: int, listener_id: str, kind: str, action: str) -> str:
    """Return the Vue ``@click`` expression that reaches NiceGUI's listener.

    Uses ``&quot;`` for inner double quotes so the expression survives
    HTML attribute parsing inside the slot template.
    """
    return (
        "$event.view.socket?.emit(&quot;event&quot;, "
        f"{{id: {tree_id}, client_id: $event.view.clientId, listener_id: &quot;{listener_id}&quot;, "
        "args: [$event.view.JSON.stringify("
        f"{{node_id: props.node.id, kind: '{kind}', action: '{action}'}}"
        ")]})"
    )


def _tree_header_slot(*, tree_id: int, listener_id: str) -> str:
    return (
        '<div class="row items-center" style="gap: 0.4rem">'
        '<q-icon v-if="props.node.type_icon" :name="props.node.type_icon" '
        ":style=\"{ color: 'var(' + props.node.type_color + ')', fontSize: '1rem', flexShrink: 0 }\"></q-icon>"
        '<img v-if="props.node.sync_icon" :src="props.node.sync_icon" '
        'style="width: 1rem; height: 1rem; flex-shrink: 0;" '
        ":title=\"props.node.sync_title || ''\" "
        ":alt=\"props.node.sync_status || ''\" />"
        "<span :data-testid=\"'tree-node-' + props.node.testid_kind\" "
        ':data-node-id="props.node.id" '
        ':data-kind="props.node.kind" '
        ":data-sync-status=\"props.node.sync_status || ''\">"
        "{{ props.node.label }}"
        "</span>"
        # Owned-equipment context menu (Edit / Remove).
        "<q-menu v-if=\"props.node.kind === 'equipment'\" context-menu auto-close "
        'data-testid="tree-context-menu">'
        "<q-list dense>"
        '<q-item clickable v-close-popup data-testid="tree-context-edit-equipment" '
        f'@click="{_ctx_emit(tree_id=tree_id, listener_id=listener_id, kind="equipment", action="edit_equipment")}">'
        "<q-item-section>Edit equipment…</q-item-section>"
        "</q-item>"
        '<q-item clickable v-close-popup data-testid="tree-context-remove-equipment" '
        f'@click="{_ctx_emit(tree_id=tree_id, listener_id=listener_id, kind="equipment", action="remove_equipment")}">'
        "<q-item-section>Remove…</q-item-section>"
        "</q-item>"
        "</q-list>"
        "</q-menu>"
        # Run context menu (Force sync / Clear verified / View log).
        "<q-menu v-if=\"props.node.kind === 'run_experimental' || props.node.kind === 'run_test'\" "
        'context-menu auto-close data-testid="run-context-menu">'
        "<q-list dense>"
        '<q-item clickable v-close-popup data-testid="run-context-force-sync" '
        f'@click="{_ctx_emit(tree_id=tree_id, listener_id=listener_id, kind="run", action="force_sync")}">'
        "<q-item-section>Force sync</q-item-section>"
        "</q-item>"
        '<q-item clickable v-close-popup data-testid="run-context-clear-verified" '
        f'@click="{_ctx_emit(tree_id=tree_id, listener_id=listener_id, kind="run", action="clear_verified")}">'
        "<q-item-section>Clear verified</q-item-section>"
        "</q-item>"
        '<q-item clickable v-close-popup data-testid="run-context-view-log" '
        f'@click="{_ctx_emit(tree_id=tree_id, listener_id=listener_id, kind="run", action="view_log")}">'
        "<q-item-section>View log</q-item-section>"
        "</q-item>"
        "</q-list>"
        "</q-menu>"
        "</div>"
    )


_TREE_DEFAULT_HEADER_SLOT = _tree_header_slot(tree_id=0, listener_id="listener")


def build_tree(
    *,
    hierarchy: dict[EquipmentNode, dict[ProjectNode, list[RunNode]]],
    on_select: Callable[[str], None] | None = None,
    on_equipment_context_action: Callable[[str, str], None] | None = None,
    on_run_context_action: Callable[[str, str], None] | None = None,
    filters: TreeFilters | None = None,
    expand_all: bool = False,
    selected_node: str | None = None,
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
    if selected_node:
        # Seed Quasar's v-model:selected so the current node renders with the
        # selected fill + 3px accent bar (the theme's .q-tree__node--selected
        # rule mirrors the file-row selection). The id can contain spaces /
        # slashes, so set the prop dict directly rather than via the
        # whitespace-splitting props-string parser (OQ-6).
        tree._props["selected"] = selected_node
    if expand_all:
        # NiceGUI's wrapper for Quasar's expandAll() method.
        tree.expand()
    if on_select is not None:

        def _selected(event: Any) -> None:
            on_select(event.value)

        tree.on_select(_selected)

    def _on_context_action(event: Any) -> None:
        detail = event.args
        if isinstance(detail, list) and detail:
            detail = detail[0]
        if not isinstance(detail, dict):
            return
        kind = detail.get("kind", "")
        node_id = detail.get("node_id", "")
        action = detail.get("action", "")
        if not node_id or not action:
            return
        if kind == "equipment" and on_equipment_context_action is not None:
            on_equipment_context_action(node_id, action)
        elif kind == "run" and on_run_context_action is not None:
            on_run_context_action(node_id, action)

    existing_listeners = set(tree._event_listeners)
    tree.on("tree-context-action", _on_context_action)
    listener_ids = [key for key in tree._event_listeners if key not in existing_listeners]
    listener_id = listener_ids[-1] if listener_ids else ""

    tree.add_slot(
        "default-header",
        _tree_header_slot(tree_id=tree.id, listener_id=listener_id),
    )
    return tree
