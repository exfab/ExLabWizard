"""Main window (Frontend Spec §3).

Layout:

* **Left drawer** -- search box + filter chips + tree.
* **Right pane** -- tabs (Details / Problems) + detail / problems view.
* **Header toolbar** -- New Project / New Run / New Test Run / Settings /
  Refresh.
* **Bottom status bar** -- Sync / Validator / LIMS segments.

Pre-Phase-13 the tray and window subprocess plumbing isn't wired here;
this page is invoked from the FastAPI app's NiceGUI mount point
(``GET /``).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from exlab_wizard.logging import get_logger
from exlab_wizard.ui import notifications
from exlab_wizard.ui.components import banner_stack, filter_chips, status_bar_segment
from exlab_wizard.ui.components.empty_state import empty_state
from exlab_wizard.ui.components.framed_pane import card_style, framed_pane
from exlab_wizard.ui.components.tree import TreeFilters, TreeNode, build_nodes, build_tree
from exlab_wizard.ui.pages.staging import StagingDockState

_log = get_logger(__name__)


@dataclass
class MainPageState:
    """Render state for the main page.

    GUI/Orchestrator Redesign §4: the legacy two-tab Details / Problems
    layout coexists with a new three-region file-explorer renderer
    (``render_file_explorer_page``); the v1 fields stay so existing
    flows keep working until Phase 9 / 10 retire them.
    """

    setup_incomplete: bool = False
    # §4.9.3 next-action discriminator, used to tailor the setup-incomplete
    # banner subline (rclone-only NAS migration, 2026-05-26).
    setup_next_action: str | None = None
    selected_node: str | None = None
    chip_state: filter_chips.ChipState = field(
        default_factory=lambda: filter_chips.initial_state(_default_chips())
    )
    active_tab: str = "details"  # "details" | "problems"
    problems_count_hard: int = 0
    problems_count_soft: int = 0
    # In-flight controller operations (Frontend §9.5). ``operations_count``
    # gates the toolbar [Operations…] button; ``operations_input_required``
    # drives the footer Sync segment's "N need input" warning.
    operations_count: int = 0
    operations_input_required: int = 0
    # §9.6 single-equipment concurrency: the three creation buttons are
    # disabled while any session is mid-flight (non-terminal).
    creation_in_flight: bool = False
    # Legacy field — the orchestrator pipeline is always active under
    # Redesign §3.1, so this always renders True in production.
    orchestrator_enabled: bool = True
    staging_dock: StagingDockState | None = None
    # Redesign §4 file-explorer additions:
    right_pane_collapsed: bool = False
    folder_feed_path: str | None = None
    selected_node_kind: str | None = None
    expand_state: dict[str, bool] = field(default_factory=dict)
    selected_node_is_received: bool = False
    """True when the selected tree node is received equipment (decision 1):
    the three creation buttons (New Project / New Run / New Test Run) are
    disabled while this is True."""
    # Redesign §4.3/§4.4 (Phase 4, Option B): a file or folder selected in the
    # centre list. ``selected_file_path`` highlights the row; ``selected_file``
    # is a render-ready payload assembled by the mount layer (no new fetch for
    # files -- resolved from the in-memory feed; a one-level scan for folders).
    # Shape: {"kind": "file"|"folder", "name", "path", ...}; see
    # metadata_pane.render_selected_file_card.
    selected_file_path: str | None = None
    selected_file: dict[str, Any] | None = None
    # §4.9 search box -> tree filter; §4.8 file-list row density ("" =
    # comfortable, "compact"). Both ride the URL (?q=, ?density=) per OQ-1/A.
    search_query: str = ""
    density: str = ""
    # Footer status-segment states (Phase 5 / §3.5.5), derived from live
    # backend signals by the mount layer
    # (status_bar_segment.derive_footer_segment_states). Default to NORMAL so a
    # half-wired backend (or a direct render in tests) shows a calm footer.
    validator_state: str = status_bar_segment.SEGMENT_NORMAL
    lims_state: str = status_bar_segment.SEGMENT_NORMAL
    staging_state: str = status_bar_segment.SEGMENT_NORMAL


def _default_chips() -> tuple[filter_chips.ChipDefinition, ...]:
    """The three default chips on the left-tree filter strip (§3.5.4)."""

    return (
        filter_chips.ChipDefinition(chip_id="active", label="Active", default_on=True),
        filter_chips.ChipDefinition(chip_id="archived", label="Archived", default_on=False),
        filter_chips.ChipDefinition(chip_id="test_runs", label="Test runs", default_on=True),
    )


def chip_state_to_tree_filters(state: filter_chips.ChipState, search: str = "") -> TreeFilters:
    """Translate a chip group state into a :class:`TreeFilters`."""

    return TreeFilters(
        active=filter_chips.is_active(state, "active"),
        archived=filter_chips.is_active(state, "archived"),
        test_runs=filter_chips.is_active(state, "test_runs"),
        search=search,
    )


def count_search_results(nodes: list[TreeNode]) -> int:
    """Count the project + run rows surfaced under the equipment roots.

    :func:`build_nodes` always emits every equipment row -- even one with no
    matching children -- so the equipment tally is not a useful "did the
    search find anything" signal. This sums the project + run rows instead:
    the number the search-result pill shows, and whose ``0`` value drives the
    no-matches state (Phase 5 / OQ-2). Pure so it is testable without NiceGUI.
    """

    total = 0
    for equipment in nodes:
        for project in equipment.children:
            total += 1 + len(project.children)
    return total


def problems_badge_text(state: MainPageState) -> str:
    """Return the count text shown on the Problems tab.

    Frontend §3.2: hard count primary, soft count secondary
    (e.g. ``3 + 12``).
    """

    if state.problems_count_soft == 0:
        return str(state.problems_count_hard)
    return f"{state.problems_count_hard} + {state.problems_count_soft}"


def setup_incomplete_banner_props(next_action: str | None = None) -> dict[str, str]:
    """Banner content for the setup-incomplete state (§3.1.4).

    ``next_action`` is the §4.9.3 next-action discriminator (e.g.
    ``configure_rclone_remote``). When supplied it tailors the subline so
    the operator knows exactly which section to open; the rclone-only
    NAS migration (2026-05-26) added the rclone-remote variant.
    """

    sublines = {
        "configure_rclone_remote": ("Configure the rclone remote (see setup docs) to begin."),
        "configure_lims": "Open Settings → LIMS to finish configuring LIMS.",
        "test_lims": "Open Settings → LIMS to finish configuring LIMS.",
        "set_paths": "Open Settings and complete the highlighted sections to begin.",
        "add_equipment": "Add an equipment in Settings to begin.",
    }
    return {
        "headline": "Setup incomplete: required configuration is missing.",
        "subline": sublines.get(
            next_action or "",
            "Open Settings and complete the highlighted sections to begin.",
        ),
        "cta_label": "Open Settings",
        "color_var": "--color-warning",
    }


def render_file_explorer_page(
    *,
    on_open_new_project: Callable[[], None],
    on_open_new_run: Callable[[], None],
    on_open_new_test_run: Callable[[], None],
    on_open_add_equipment: Callable[[], None],
    on_open_settings: Callable[[], None],
    on_refresh: Callable[[], None],
    on_select_node: Callable[[str], None],
    on_open_operations: Callable[[], None] | None = None,
    on_navigate_breadcrumb: Callable[[str], None] | None = None,
    on_toggle_right_pane: Callable[[], None] | None = None,
    on_run_staging_action: Callable[[str, str], None] | None = None,
    on_clear_verified: Callable[[], None] | None = None,
    on_tree_context_action: Callable[[str, str], None] | None = None,
    on_file_context_action: Callable[[Any, str], None] | None = None,
    on_select_file: Callable[[Any], None] | None = None,
    on_refresh_folder: Callable[[], None] | None = None,
    on_search: Callable[[str], None] | None = None,
    state: MainPageState | None = None,
    hierarchy: dict | None = None,
    file_list_entries: list[Any] | None = None,
    metadata_payload: dict[str, Any] | None = None,
    tree_expand_all: bool = False,
) -> Any:  # pragma: no cover -- NiceGUI render, driven by e2e
    """Render the rebuilt three-region file-explorer main window.

    GUI/Orchestrator Redesign §4. Header toolbar + breadcrumb + splitter
    (tree | live file list | metadata/problems pane) + footer status
    bar. The render function stays free of session-store / API deps:
    state is injected; the caller wires the callbacks.

    ``file_list_entries`` and ``metadata_payload`` carry the data the
    centre and right panes display when a node is selected; both are
    optional so unit tests can render the empty-state. The mount layer
    sources them from the live FolderFeed payload and the per-node
    metadata builder respectively.

    ``tree_expand_all`` forwards to :func:`build_tree`'s ``expand_all``
    kwarg -- only set by e2e tests that need every node visible in the
    DOM up front.
    """
    s = state or MainPageState()

    try:
        from nicegui import ui
    except Exception:
        return {"state": s}

    from exlab_wizard.ui.components.breadcrumb import render_breadcrumb

    # Pin the page to the viewport so the three panes fill the height between
    # the fixed header and footer and scroll *internally* rather than growing
    # the page. NiceGUI's q-layout / q-page chain is min-height-driven (content-
    # sized) by default; these page-scoped overrides give it a definite height
    # and flex-fill down to the splitter, whose panes then scroll within their
    # own overflow:auto bodies.
    ui.query(".q-page-container").style(
        "height: 100vh; display: flex; flex-direction: column; overflow: hidden;"
    )
    ui.query(".q-page").style("flex: 1 1 0; min-height: 0; display: flex; flex-direction: column;")
    ui.query(".nicegui-content").style("flex: 1 1 0; min-height: 0;")

    with (
        ui.header()
        .classes("items-center")
        .style(
            "background: var(--color-surface); "
            "border-bottom: 1px solid var(--color-rule); "
            "padding: var(--sp-3) var(--sp-6);"
        )
    ):
        ui.label("ExLab-Wizard").style(
            "font-family: var(--font-display); font-size: var(--text-md); "
            "color: var(--color-heading); font-weight: 600;"
        )
        ui.space()
        # Redesign decision 1: creation buttons disable on received-equipment.
        np_btn = ui.button("New Project", on_click=lambda _evt: on_open_new_project()).props(
            'color=primary data-testid="toolbar-new-project"'
        )
        nr_btn = ui.button("New Run", on_click=lambda _evt: on_open_new_run()).props(
            'color=primary data-testid="toolbar-new-run"'
        )
        ntr_btn = ui.button("New Test Run", on_click=lambda _evt: on_open_new_test_run()).props(
            'color=warning data-testid="toolbar-new-test-run"'
        )
        # Creation is disabled on received-equipment nodes (decision 1) and
        # while any session is mid-flight (single-equipment concurrency, §9.6).
        if s.selected_node_is_received or s.creation_in_flight:
            for btn in (np_btn, nr_btn, ntr_btn):
                btn.props("disable")
            if s.creation_in_flight and not s.selected_node_is_received:
                np_btn.tooltip("A creation is already in progress")
        ui.button("Add Equipment", on_click=lambda _evt: on_open_add_equipment()).props(
            'color=primary data-testid="toolbar-add-equipment"'
        )
        # Group divider: the creation actions (New Project / New Run / New Test
        # Run / Add Equipment) sit left of this rule; the utility actions
        # (Operations / Refresh / Settings) sit right of it, so the toolbar
        # reads as two groups. Every button's order + testid is unchanged; the
        # margin supplies the inter-group gap.
        ui.separator().props('vertical data-testid="toolbar-group-divider"').style(
            "height: 1.5rem; margin: 0 var(--sp-2, 0.5rem); background: var(--color-rule, #e8ecf2);"
        )
        # [Operations…] surfaces only while ≥1 operation is in flight
        # (Frontend §9.5). Label carries the count; a warning color flags
        # any suspended (INPUT_REQUIRED) session needing an answer.
        if on_open_operations is not None and s.operations_count > 0:
            ops_color = "warning" if s.operations_input_required > 0 else "primary"
            ui.button(
                f"Operations ({s.operations_count})",
                on_click=lambda _evt: on_open_operations(),
            ).props(f'flat color={ops_color} data-testid="toolbar-operations"')
        ui.button("Refresh", on_click=lambda _evt: on_refresh()).props(
            'flat data-testid="toolbar-refresh"'
        )
        ui.button("Settings", on_click=lambda _evt: on_open_settings()).props(
            'flat data-testid="toolbar-settings"'
        )

    render_breadcrumb(
        selected_node=s.selected_node,
        on_navigate=on_navigate_breadcrumb,
    )

    if s.setup_incomplete:
        notifications.show_banner(
            notifications.BannerId.SETUP_INCOMPLETE,
            container=notifications.ContainerId.GLOBAL,
            severity=notifications.Severity.WARNING,
            message=setup_incomplete_banner_props(s.setup_next_action)["subline"],
            action=notifications.ActionSpec(
                label="Open Settings",
                on_click=on_open_settings,
            ),
            dismissible=False,
        )
    else:
        notifications.clear_banner(notifications.BannerId.SETUP_INCOMPLETE)
    banner_stack.banner_stack(notifications.ContainerId.GLOBAL)

    # Run-row context-menu items share the metadata-pane's
    # (path, action) callback signature, so the tree's run-context
    # actions route into on_run_staging_action via this thin shim.
    def _route_run_context(node_id: str, action: str) -> None:
        if on_run_staging_action is not None:
            on_run_staging_action(node_id, action)

    # Splitter holds tree | (file list + metadata pane). The right-pane
    # collapse toggle is wired by the caller via on_toggle_right_pane.
    with (
        ui.splitter(value=20)
        .classes("w-full")
        .style("flex: 1 1 0; min-height: 0; gap: var(--sp-3, 0.75rem);")
    ) as outer_split:
        with (
            outer_split.before,
            framed_pane("Explorer", testid="explorer-pane"),
            ui.column().classes("w-full").style("gap: 0.5rem;"),
        ):
            tree_filters = chip_state_to_tree_filters(s.chip_state, search=s.search_query)
            # Search box (§4.9 / OQ-2): the clear affordance (clearable) wipes
            # it; Quasar's `debounce` coalesces keystrokes so the page re-navigates
            # (?q=) once the operator pauses -- the same URL/navigate model the
            # filter chips and row selection already use. The narrowed local
            # keeps mypy happy about the optional callback inside the closure.
            search_cb = on_search

            def _on_search_change(event: Any) -> None:
                if search_cb is None:
                    return
                search_cb((event.value or "").strip())

            ui.input(
                label="Search",
                value=s.search_query,
                on_change=_on_search_change if on_search is not None else None,
            ).props('data-testid="main-search" clearable debounce=300').style("width: 100%;")
            # Result count / no-matches affordance -- shown only while a query
            # is active, sourced from the same build_nodes the tree renders so
            # the tally can't drift from what's on screen.
            if s.search_query:
                _matches = count_search_results(
                    build_nodes(hierarchy=hierarchy or {}, filters=tree_filters)
                )
                _search_hint_style = (
                    "color: var(--color-muted); font-size: var(--text-xs); padding: 0 var(--sp-1);"
                )
                if _matches == 0:
                    ui.label("No matches.").props('data-testid="main-search-no-matches"').style(
                        _search_hint_style
                    )
                else:
                    ui.label(f"{_matches} result{'s' if _matches != 1 else ''}").props(
                        'data-testid="main-search-count"'
                    ).style(_search_hint_style)
            filter_chips.filter_chips(_default_chips(), state=s.chip_state)
            build_tree(
                hierarchy=hierarchy or {},
                filters=tree_filters,
                on_select=on_select_node,
                on_equipment_context_action=on_tree_context_action,
                on_run_context_action=(
                    _route_run_context if on_run_staging_action is not None else None
                ),
                expand_all=tree_expand_all,
            )
        # The Files pane fills the splitter's right side; the metadata pane
        # floats over its right edge as an overlay popover (no permanent docked
        # column). The vertical "Metadata" tab toggles it via the right_pane URL
        # param. position:relative anchors the absolutely-positioned popover+tab.
        with (
            outer_split.after,
            ui.element("div")
            .classes("w-full h-full")
            .style(
                "position: relative; display: flex; flex-direction: row; "
                "flex-wrap: nowrap; align-items: stretch;"
            ),
        ):
            with ui.element("div").style("flex: 1 1 auto; min-width: 0; height: 100%;"):
                files_count = f"{len(file_list_entries)} items" if file_list_entries else None

                def _files_header_extra() -> None:
                    # OQ-1/A mitigation: a per-folder refresh, distinct from the
                    # toolbar's "Refresh everything" -- it re-scans only the open
                    # folder then re-renders (mount._refresh_selected_folder).
                    # Grouped on the left beside the count pill (count_left=True).
                    refresh = on_refresh_folder
                    if refresh is None:
                        return
                    ui.button(icon="refresh", on_click=lambda _evt: refresh()).props(
                        'flat dense round size=sm data-testid="files-refresh" '
                        'title="Refresh this folder"'
                    ).style("color: var(--color-muted, #8892a4);")

                with framed_pane(
                    "Files",
                    count=files_count,
                    testid="files-pane",
                    header_extra=_files_header_extra,
                    count_left=True,
                ):
                    _render_centre_file_list(
                        s,
                        file_list_entries=file_list_entries,
                        on_file_context_action=on_file_context_action,
                        on_select_file=on_select_file,
                    )
            # Vertical "Metadata" tab: a chevron above a vertical label in a
            # raised box. Absolutely positioned -- on the popover's left edge
            # when open (overlapping it, painted just behind so the right half
            # tucks under the panel), or parked at the container's right edge
            # when the popover is closed.
            if on_toggle_right_pane is not None:
                collapsed = s.right_pane_collapsed
                chevron = "◀" if collapsed else "▶"
                tab_label = "Metadata"
                tab_pos = (
                    "right: 0; z-index: 21;"
                    if collapsed
                    else "right: calc(40% - 16px); z-index: 19;"
                )
                toggle = (
                    ui.button(on_click=lambda _evt: on_toggle_right_pane())
                    .props(
                        'flat dense no-caps data-testid="toggle-right-pane" '
                        'aria-label="Toggle metadata pane" title="Toggle metadata pane"'
                    )
                    .style(
                        # Absolute so it anchors to the popover's left edge (open)
                        # or the container's right edge (closed). Theme vars keep
                        # literal fallbacks for robustness.
                        f"position: absolute; top: 64px; {tab_pos} "
                        "min-width: 0; width: 40px; height: 190px; padding: 0; "
                        "color: var(--color-muted, #8892a4);"
                    )
                )
                with (
                    toggle,
                    ui.column().style(
                        # Content hugs the left edge so the chevron + vertical
                        # label stay on the visible left half (the right half is
                        # tucked behind the metadata pane). Only the left corners
                        # are rounded so the right edge reads as merging into the
                        # pane.
                        "align-items: flex-start; gap: 4px; flex-wrap: nowrap; "
                        "height: 100%; width: 100%; padding: 8px 3px; "
                        "background: var(--color-surface, #ffffff); "
                        "border: 1px solid var(--color-border, #dde3ed); "
                        "border-radius: 6px 0 0 6px; "
                        "box-shadow: 0 1px 3px rgba(0, 54, 96, 0.12);"
                    ),
                ):
                    ui.label(chevron).style(
                        "flex: 0 0 auto; font-size: 12px; line-height: 1; "
                        "color: var(--color-muted, #8892a4);"
                    )
                    # Vertical label via writing-mode (robust -- no transform-box
                    # clipping): vertical-rl + rotate(180deg) reads bottom-to-top,
                    # matching the prior orientation. The column's
                    # align-items:flex-start keeps it on the visible left half.
                    ui.label(tab_label).style(
                        "writing-mode: vertical-rl; transform: rotate(180deg); "
                        "white-space: nowrap; font-size: 11px; letter-spacing: 0.05em; "
                        "text-transform: none; color: var(--color-muted, #8892a4);"
                    )
            if not s.right_pane_collapsed:
                # Metadata floats as an overlay popover over the right of the
                # Files pane (z-index above it, strong left shadow so it reads as
                # raised). It keeps its own tabs in place of a title strip;
                # card_style() ends with ';' so the appended overrides
                # concatenate to valid CSS.
                with (
                    ui.element("div")
                    .props('data-testid="metadata-pane-card"')
                    .style(
                        "position: absolute; top: 0; right: 0; height: 100%; "
                        "width: 40%; z-index: 20; min-width: 0; "
                        f"{card_style()} overflow: auto; "
                        "box-shadow: -4px 0 16px rgba(0, 54, 96, 0.15);"
                    )
                ):
                    _render_right_pane(
                        s,
                        metadata_payload=metadata_payload,
                        on_run_staging_action=on_run_staging_action,
                    )

    if not s.setup_incomplete:
        with (
            # Footer reads as a framed bar to match the panes: surface fill,
            # hairline border, soft shadow, small margins so it sits as a
            # distinct card rather than bleeding to the window edges.
            ui.footer().style(
                "background: var(--color-surface, #ffffff); "
                "border: 1px solid var(--color-border, #dde3ed); "
                "border-radius: var(--radius-md, 10px); "
                "box-shadow: var(--shadow-sm, 0 1px 3px rgba(0,54,96,0.07)); "
                "margin: var(--sp-2, 0.5rem); "
                "padding: 0 var(--sp-4, 1rem); min-height: 24px;"
            ),
            ui.row().classes("items-center w-full"),
        ):
            # Sync segment doubles as the Operations entry point: when any
            # session is suspended awaiting input it flips to a warning
            # "N operations need input" and opens the same modal (§3.5.5).
            if s.operations_input_required > 0:
                status_bar_segment.status_bar_segment(
                    label=f"{s.operations_input_required} operations need input",
                    state=status_bar_segment.SEGMENT_WARNING,
                    on_click=on_open_operations,
                )
            else:
                # Only clickable when there is something to show, so a click
                # never opens an empty Operations panel.
                status_bar_segment.status_bar_segment(
                    label="Sync",
                    state=status_bar_segment.SEGMENT_NORMAL,
                    on_click=on_open_operations if s.operations_count > 0 else None,
                )
            # Validator / LIMS / Staging states are derived from live backend
            # signals by the mount (derive_footer_segment_states): Validator
            # warns on a hard finding, LIMS goes danger when the endpoint is
            # unreachable. They default to NORMAL on a half-wired backend.
            status_bar_segment.status_bar_segment(
                label="Validator",
                state=s.validator_state,
            )
            status_bar_segment.status_bar_segment(
                label="LIMS",
                state=s.lims_state,
            )
            # Footer Staging segment with bulk-clear-verified popover
            # (§4.6: the bottom dock's bulk action relocates here).
            status_bar_segment.status_bar_segment(
                label="Staging",
                state=s.staging_state,
            ).props('data-testid="footer-staging-segment"')
            if on_clear_verified is not None:
                ui.button("Clear verified runs", on_click=lambda _evt: on_clear_verified()).props(
                    'flat data-testid="footer-clear-verified"'
                )


def _render_centre_file_list(
    state: MainPageState,
    *,
    file_list_entries: list[Any] | None = None,
    on_file_context_action: Callable[[Any, str], None] | None = None,
    on_select_file: Callable[[Any], None] | None = None,
) -> None:  # pragma: no cover -- NiceGUI render, driven by e2e
    """Render the centre-pane file list (Redesign §4.3).

    Each row carries a right-click context menu (*Open in OS* /
    *Copy path*) when ``on_file_context_action`` is wired. Single-click
    selection (``on_select_file``) drives the right-pane metadata sub-card
    and highlights the selected row (Phase 4 / Option B).
    """
    from exlab_wizard.ui.components.file_list import (
        FileListState,
        render_file_list,
    )

    # No NiceGUI guard here: empty_state() and render_file_list() each no-op
    # outside an app context, and FileListState is a plain dataclass, so the
    # function degrades safely without an explicit ``ui`` import.
    if state.folder_feed_path is None:
        empty_state(
            icon="account_tree",
            message="Select a folder in the tree to see its contents.",
            testid="file-list-empty",
        )
        return
    fl_state = FileListState(
        path=state.folder_feed_path,
        entries=list(file_list_entries or []),
        selected_path=state.selected_file_path,
    )
    render_file_list(
        state=fl_state,
        on_context_menu=on_file_context_action,
        on_select=on_select_file,
    )


def _render_right_pane(
    state: MainPageState,
    *,
    on_run_staging_action: Callable[[str, str], None] | None = None,
    metadata_payload: dict[str, Any] | None = None,
) -> None:  # pragma: no cover -- NiceGUI render, driven by e2e
    """Render the right Metadata / Problems pane (Redesign §4.4)."""
    from exlab_wizard.ui.components.metadata_pane import (
        MetadataPaneState,
        render_metadata_pane,
    )

    try:
        from nicegui import ui
    except Exception:
        return
    with ui.tabs() as tabs:
        ui.tab("metadata", "Metadata").props('data-testid="tab-metadata"')
        ui.tab(
            "problems",
            f"Problems ({problems_badge_text(state)})",
        ).props('data-testid="tab-problems"')
    with ui.tab_panels(tabs, value="metadata").classes("w-full"):
        with ui.tab_panel("metadata"):
            mp_state = MetadataPaneState(
                selected_node=state.selected_node,
                node_kind=state.selected_node_kind,
                payload=dict(metadata_payload or {}),
                selected_file=state.selected_file,
            )
            render_metadata_pane(
                state=mp_state,
                on_run_staging_action=on_run_staging_action,
            )
        with ui.tab_panel("problems"):
            total = state.problems_count_hard + state.problems_count_soft
            ui.label(
                f"{total} findings ({state.problems_count_hard} hard, "
                f"{state.problems_count_soft} soft)",
            ).props('data-testid="problems-summary"').style(
                "font-family: var(--font-mono); color: var(--color-muted);"
            )
