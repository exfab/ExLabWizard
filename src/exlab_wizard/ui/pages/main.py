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
from exlab_wizard.ui.components.tree import TreeFilters, build_tree
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
    ``set_nas_credentials``). When supplied it tailors the subline so
    the operator knows exactly which section to open; the rclone-only
    NAS migration (2026-05-26) added the NAS-credentials variant.
    """

    sublines = {
        "set_nas_credentials": ("Set the NAS password in Settings → NAS Credentials to begin."),
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
        if s.selected_node_is_received:
            for btn in (np_btn, nr_btn, ntr_btn):
                btn.props("disable")
        ui.button("Add Equipment", on_click=lambda _evt: on_open_add_equipment()).props(
            'color=primary data-testid="toolbar-add-equipment"'
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
    with ui.splitter(value=20).classes("w-full h-full") as outer_split:
        with outer_split.before, ui.column().classes("w-full p-3").style("gap: 0.5rem;"):
            ui.input(label="Search").props('data-testid="main-search"').style("width: 100%;")
            filter_chips.filter_chips(_default_chips(), state=s.chip_state)
            build_tree(
                hierarchy=hierarchy or {},
                filters=chip_state_to_tree_filters(s.chip_state),
                on_select=on_select_node,
                on_equipment_context_action=on_tree_context_action,
                on_run_context_action=(
                    _route_run_context if on_run_staging_action is not None else None
                ),
                expand_all=tree_expand_all,
            )
        # Flex row: the centre file list grows to fill, a tall vertical
        # toggle tab sits on the metadata pane's left edge, then the
        # metadata pane itself. The tab lives *between* the two panes, so
        # it travels horizontally with the pane -- open, it hugs the pane's
        # left border; collapsed (pane unrendered) the growing file list
        # pushes it to the right screen edge.
        #
        # The tab is TOP-aligned (align-self:flex-start), not centred. The
        # splitter panel's top is identical in both states, but its height
        # differs (the metadata pane adds height when open), so a centred
        # tab landed at a different Y per state -- that vertical shift was
        # the up/down "jump". Pinning to the top ties the tab's Y to the
        # constant panel top, so it holds its line on toggle.
        with outer_split.after, ui.element("div").classes("w-full h-full").style(
            "display: flex; flex-direction: row; flex-wrap: nowrap; align-items: stretch;"
        ):
            with ui.element("div").style(
                "flex: 1 1 auto; min-width: 0; height: 100%; overflow: auto;"
            ):
                _render_centre_file_list(
                    s,
                    file_list_entries=file_list_entries,
                    on_file_context_action=on_file_context_action,
                )
            # Vertical collapse/expand tab: a chevron stacked above a rotated
            # text label, inside one tall box with a raised-surface background
            # so it reads as a distinct tab. The glyph points the way the pane
            # will move -- right-chevron collapses it away, left-chevron pulls
            # it back; the label names the action. Callback wired by the mount
            # layer.
            if on_toggle_right_pane is not None:
                collapsed = s.right_pane_collapsed
                chevron = "◀" if collapsed else "▶"
                tab_label = "Expand metadata" if collapsed else "Collapse metadata"
                # The button stays `flat` (Quasar forces its own background to
                # transparent !important on flat buttons, so the tab fill must
                # live on an inner element, not the button). The button is just
                # the sized, padding-free click target; the inner column paints
                # the raised-surface tab.
                #
                # Every theme var carries a literal fallback: register_theme()
                # is not injected on every route, so a bare var(--color-surface)
                # resolves to empty and the whole declaration is dropped (no
                # fill). The fallbacks make the tab render regardless.
                toggle = (
                    ui.button(on_click=lambda _evt: on_toggle_right_pane())
                    .props(
                        'flat dense no-caps data-testid="toggle-right-pane" '
                        'aria-label="Toggle metadata pane" title="Toggle metadata pane"'
                    )
                    .style(
                        "align-self: flex-start; flex: 0 0 auto; margin: 8px 2px 0 2px; "
                        "min-width: 0; width: 40px; height: 190px; padding: 0; "
                        "color: var(--color-muted, #8892a4);"
                    )
                )
                with toggle, ui.column().style(
                    "align-items: center; gap: 6px; flex-wrap: nowrap; "
                    "height: 100%; width: 100%; padding: 8px 2px; "
                    # Surface fill + border + soft shadow so the chevron and
                    # label read as a distinct raised tab against the page.
                    "background: var(--color-surface, #ffffff); "
                    "border: 1px solid var(--color-border, #dde3ed); border-radius: 6px; "
                    "box-shadow: 0 1px 3px rgba(0, 54, 96, 0.12);"
                ):
                    ui.label(chevron).style(
                        "flex: 0 0 auto; font-size: 12px; line-height: 1; "
                        "color: var(--color-muted, #8892a4);"
                    )
                    # The label is rotated 270deg (reads bottom-to-top). A
                    # transform keeps the element's layout box horizontal, so
                    # this flex-grow wrapper supplies the vertical room and
                    # centres the rotated text within it.
                    with ui.element("div").style(
                        "flex: 1 1 auto; width: 100%; display: flex; "
                        "align-items: center; justify-content: center; overflow: hidden;"
                    ):
                        ui.label(tab_label).style(
                            "transform: rotate(270deg); white-space: nowrap; "
                            "font-size: 11px; letter-spacing: 0.05em; text-transform: none; "
                            "color: var(--color-muted, #8892a4);"
                        )
            if not s.right_pane_collapsed:
                with ui.element("div").style(
                    "flex: 0 0 40%; min-width: 0; height: 100%; overflow: auto;"
                ):
                    _render_right_pane(
                        s,
                        metadata_payload=metadata_payload,
                        on_run_staging_action=on_run_staging_action,
                    )

    if not s.setup_incomplete:
        with (
            ui.footer().style(
                "background: var(--color-bg); "
                "border-top: 1px solid var(--color-rule); "
                "padding: 0 var(--sp-4); min-height: 24px;"
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
                status_bar_segment.status_bar_segment(
                    label="Sync",
                    state=status_bar_segment.SEGMENT_NORMAL,
                    on_click=on_open_operations,
                )
            status_bar_segment.status_bar_segment(
                label="Validator",
                state=status_bar_segment.SEGMENT_NORMAL,
            )
            status_bar_segment.status_bar_segment(
                label="LIMS",
                state=status_bar_segment.SEGMENT_NORMAL,
            )
            # Footer Staging segment with bulk-clear-verified popover
            # (§4.6: the bottom dock's bulk action relocates here).
            status_bar_segment.status_bar_segment(
                label="Staging",
                state=status_bar_segment.SEGMENT_NORMAL,
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
) -> None:  # pragma: no cover -- NiceGUI render, driven by e2e
    """Render the centre-pane file list (Redesign §4.3).

    Each row carries a right-click context menu (*Open in OS* /
    *Copy path*) when ``on_file_context_action`` is wired.
    """
    from exlab_wizard.ui.components.file_list import (
        FileListState,
        render_file_list,
    )

    try:
        from nicegui import ui
    except Exception:
        return
    if state.folder_feed_path is None:
        ui.label("Select a folder in the tree to see its contents.").style(
            "color: var(--color-muted); padding: var(--sp-3);"
        ).props('data-testid="file-list-empty"')
        return
    fl_state = FileListState(
        path=state.folder_feed_path,
        entries=list(file_list_entries or []),
    )
    render_file_list(state=fl_state, on_context_menu=on_file_context_action)


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
            )
            render_metadata_pane(
                state=mp_state,
                on_run_staging_action=on_run_staging_action,
            )
        with ui.tab_panel("problems"):
            ui.label(
                f"Showing 0 of {state.problems_count_hard + state.problems_count_soft} findings",
            ).props('data-testid="problems-summary"').style(
                "font-family: var(--font-mono); color: var(--color-muted);"
            )
