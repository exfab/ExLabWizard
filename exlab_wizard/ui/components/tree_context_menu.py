"""Tree context-menu renderers.

GUI/Orchestrator Redesign §4.6 / decision 4A / §9.1.

Two right-click context menus live on tree nodes:

* **Owned-equipment node** — *Edit equipment…* and *Remove…* both deep-
  link into Settings → Equipment List. Add lives on the toolbar
  (decision 4A); these stay in Settings.
* **Run node** — *Force sync*, *Clear verified*, *View log*. These are
  the relocated bottom-dock per-run actions (§4.6) and are also
  surfaced on the run's Metadata pane.

The renderers are NiceGUI thin wrappers around ``ui.context_menu()``;
both take a single callable (``on_tree_context_action`` /
``on_run_staging_action``) that the mount layer wires.

Received-equipment nodes deliberately have no context menu — they
aren't editable from this device (Redesign §3.3 + decision 3).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

__all__ = [
    "RUN_CONTEXT_CLEAR_VERIFIED",
    "RUN_CONTEXT_FORCE_SYNC",
    "RUN_CONTEXT_VIEW_LOG",
    "TREE_CONTEXT_EDIT_EQUIPMENT",
    "TREE_CONTEXT_REMOVE_EQUIPMENT",
    "render_equipment_context_menu",
    "render_run_context_menu",
]


# Action discriminators consumed by the on_tree_context_action callback.
TREE_CONTEXT_EDIT_EQUIPMENT = "edit_equipment"
TREE_CONTEXT_REMOVE_EQUIPMENT = "remove_equipment"

# Action discriminators consumed by the on_run_staging_action callback.
# These are the same strings the metadata pane surfaces so a single
# router on the mount side handles both.
RUN_CONTEXT_FORCE_SYNC = "force_sync"
RUN_CONTEXT_CLEAR_VERIFIED = "clear_verified"
RUN_CONTEXT_VIEW_LOG = "view_log"


def render_equipment_context_menu(
    *,
    equipment_id: str,
    on_action: Callable[[str, str], None],
) -> Any:  # pragma: no cover -- NiceGUI render, driven by e2e
    """Render the right-click menu for an owned-equipment tree node.

    The callback receives ``(equipment_id, action)``; the mount layer
    routes both actions to the Settings → Equipment List page with the
    equipment pre-selected (decision 4A).
    """
    try:
        from nicegui import ui
    except Exception:
        return None
    with ui.context_menu().props(
        f'data-testid="tree-context-menu" data-node-id="{equipment_id}"'
    ) as menu:
        ui.menu_item("Edit equipment…").props('data-testid="tree-context-edit-equipment"').on(
            "click",
            lambda _evt: on_action(equipment_id, TREE_CONTEXT_EDIT_EQUIPMENT),
        )
        ui.menu_item("Remove…").props('data-testid="tree-context-remove-equipment"').on(
            "click",
            lambda _evt: on_action(equipment_id, TREE_CONTEXT_REMOVE_EQUIPMENT),
        )
    return menu


def render_run_context_menu(
    *,
    run_path: str,
    on_action: Callable[[str, str], None],
) -> Any:  # pragma: no cover -- NiceGUI render, driven by e2e
    """Render the right-click menu for a run tree node.

    The callback receives ``(run_path, action)``. The three actions
    match ``RUN_CONTEXT_FORCE_SYNC`` / ``RUN_CONTEXT_CLEAR_VERIFIED`` /
    ``RUN_CONTEXT_VIEW_LOG`` and are the relocated bottom-dock per-run
    actions (Redesign §4.6).
    """
    try:
        from nicegui import ui
    except Exception:
        return None
    with ui.context_menu().props(
        f'data-testid="run-context-menu" data-run-path="{run_path}"'
    ) as menu:
        ui.menu_item("Force sync").props('data-testid="run-context-force-sync"').on(
            "click",
            lambda _evt: on_action(run_path, RUN_CONTEXT_FORCE_SYNC),
        )
        ui.menu_item("Clear verified").props('data-testid="run-context-clear-verified"').on(
            "click",
            lambda _evt: on_action(run_path, RUN_CONTEXT_CLEAR_VERIFIED),
        )
        ui.menu_item("View log").props('data-testid="run-context-view-log"').on(
            "click",
            lambda _evt: on_action(run_path, RUN_CONTEXT_VIEW_LOG),
        )
    return menu
