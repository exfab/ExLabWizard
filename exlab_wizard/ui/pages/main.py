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
from exlab_wizard.ui.pages.staging import StagingDockState, render_staging_dock

_log = get_logger(__name__)


@dataclass
class MainPageState:
    """Render state for the main page."""

    setup_incomplete: bool = False
    selected_node: str | None = None
    chip_state: filter_chips.ChipState = field(
        default_factory=lambda: filter_chips.initial_state(_default_chips())
    )
    active_tab: str = "details"  # "details" | "problems"
    problems_count_hard: int = 0
    problems_count_soft: int = 0
    orchestrator_enabled: bool = False
    """When True the staging dock is rendered below the main content (§13.8)."""
    staging_dock: StagingDockState | None = None
    """Staging-panel state -- None means render an empty dock."""


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


def setup_incomplete_banner_props() -> dict[str, str]:
    """Banner content for the setup-incomplete state (§3.1.4)."""

    return {
        "headline": "Setup incomplete: required configuration is missing.",
        "subline": "Open Settings and complete the highlighted sections to begin.",
        "cta_label": "Open Settings",
        "color_var": "--color-warning",
    }


def render_main_page(
    *,
    on_open_new_project: Callable[[], None],
    on_open_new_run: Callable[[], None],
    on_open_new_test_run: Callable[[], None],
    on_open_settings: Callable[[], None],
    on_refresh: Callable[[], None],
    state: MainPageState | None = None,
    hierarchy: dict | None = None,
    tree_expand_all: bool = False,
) -> Any:
    """Render the main window.

    ``state`` and ``hierarchy`` are injected by the caller; this lets the
    page stay free of any session-store / SSE dependency at unit-test time.

    ``tree_expand_all`` forwards to :func:`build_tree`'s ``expand_all``
    kwarg -- only set by e2e tests that need every node visible in the
    DOM up front.
    """

    s = state or MainPageState()
    chips = _default_chips()

    try:
        from nicegui import ui
    except Exception:
        return {"state": s, "chips": chips}

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
            "font-family: var(--font-display); "
            "font-size: var(--text-md); "
            "color: var(--color-heading); "
            "font-weight: 600;"
        )
        ui.space()
        ui.button(
            "New Project",
            on_click=lambda _evt: on_open_new_project(),
        ).props('color=primary data-testid="toolbar-new-project"')
        ui.button(
            "New Run",
            on_click=lambda _evt: on_open_new_run(),
        ).props('color=primary data-testid="toolbar-new-run"')
        ui.button(
            "New Test Run",
            on_click=lambda _evt: on_open_new_test_run(),
        ).props('color=warning data-testid="toolbar-new-test-run"')
        ui.button(
            "Settings",
            on_click=lambda _evt: on_open_settings(),
        ).props('flat data-testid="toolbar-settings"')
        ui.button(
            "Refresh",
            on_click=lambda _evt: on_refresh(),
        ).props('flat data-testid="toolbar-refresh"')

    if s.setup_incomplete:
        notifications.show_banner(
            notifications.BannerId.SETUP_INCOMPLETE,
            container=notifications.ContainerId.GLOBAL,
            severity=notifications.Severity.WARNING,
            message=setup_incomplete_banner_props()["subline"],
            action=notifications.ActionSpec(
                label="Open Settings",
                on_click=on_open_settings,
            ),
            dismissible=False,
        )
    else:
        notifications.clear_banner(notifications.BannerId.SETUP_INCOMPLETE)
    banner_stack.banner_stack(notifications.ContainerId.GLOBAL)
    # Stable testid alias for the setup-incomplete banner so e2e flows
    # can wait on a single locator regardless of banner order.
    if s.setup_incomplete:
        ui.element("div").props('data-testid="setup-incomplete-banner"').style("display:none;")

    with ui.splitter(value=30).classes("w-full h-full") as split:
        with split.before, ui.column().classes("w-full p-3").style("gap: 0.5rem;"):
            ui.input(label="Search").props('data-testid="main-search"').style("width: 100%;")
            filter_chips.filter_chips(
                chips,
                state=s.chip_state,
            )
            build_tree(
                hierarchy=hierarchy or {},
                filters=chip_state_to_tree_filters(s.chip_state),
                expand_all=tree_expand_all,
            )
        with split.after:
            with ui.tabs() as tabs:
                ui.tab("details", "Details").props('data-testid="tab-details"')
                ui.tab(
                    "problems",
                    f"Problems ({problems_badge_text(s)})",
                ).props('data-testid="tab-problems"')
            with ui.tab_panels(tabs, value=s.active_tab).classes("w-full"):
                with ui.tab_panel("details"):
                    ui.label("Select a node to see details.").props(
                        'data-testid="details-empty"'
                    ).style("color: var(--color-muted);")
                with ui.tab_panel("problems"):
                    ui.label(
                        f"Showing 0 of {s.problems_count_hard + s.problems_count_soft} findings",
                    ).props('data-testid="problems-summary"').style(
                        "font-family: var(--font-mono); color: var(--color-muted);"
                    )

    if s.orchestrator_enabled:
        # Mount the bottom-dock staging panel when orchestrator mode is active
        # (§13.8). The dock is non-collapsible and ~120 px tall; see
        # ``ui/pages/staging.py`` for the per-row actions and column layout.
        dock_state = s.staging_dock or StagingDockState(rows=[])
        render_staging_dock(dock_state)

    if not s.setup_incomplete:
        with (
            ui.footer().style(
                "background: var(--color-bg); "
                "border-top: 1px solid var(--color-rule); "
                "padding: 0 var(--sp-4); "
                "min-height: 24px;"
            ),
            ui.row().classes("items-center w-full"),
        ):
            status_bar_segment.status_bar_segment(
                label="All synced",
                state=status_bar_segment.SEGMENT_NORMAL,
            )
            status_bar_segment.status_bar_segment(
                label="Last audit: --",
                state=status_bar_segment.SEGMENT_NORMAL,
            )
            status_bar_segment.status_bar_segment(
                label="LIMS: --",
                state=status_bar_segment.SEGMENT_NORMAL,
            )
    return tabs
