"""Breadcrumb bar for the rebuilt main window.

GUI/Orchestrator Redesign §4.1 / §4.7 / decision 7A. Renders a path of
clickable segments derived from ``selected_node``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BreadcrumbSegment:
    """One clickable segment in the breadcrumb bar."""

    label: str
    node_id: str
    """Tree node id the segment selects when clicked."""


def segments_from_node_id(selected_node: str | None) -> list[BreadcrumbSegment]:
    """Decompose a tree-node id into clickable segments.

    Tree ids are slash-joined strings (``equip/proj/Runs/Run_...``). The
    leading device label is conceptually the root; we don't render it
    explicitly here. Returns a left-to-right list of segments where each
    segment's ``node_id`` is the ancestor id needed to navigate to it.
    """
    if not selected_node:
        return []
    parts = [p for p in selected_node.split("/") if p]
    out: list[BreadcrumbSegment] = []
    for idx, part in enumerate(parts):
        ancestor_id = "/".join(parts[: idx + 1])
        out.append(BreadcrumbSegment(label=part, node_id=ancestor_id))
    return out


def render_breadcrumb(
    *,
    selected_node: str | None,
    on_navigate: Callable[[str], None] | None = None,
) -> Any:
    """Render the breadcrumb row. Pure render function.

    Each segment is a clickable label; the callback receives the
    segment's ``node_id`` so the caller can re-emit selection through
    the standard ``on_select_node`` handler (Redesign §9.1).
    """
    segments = segments_from_node_id(selected_node)
    try:
        from nicegui import ui
    except Exception:
        return {"segments": segments}

    with ui.row().classes("items-center").style(
        "padding: var(--sp-2) var(--sp-4); "
        "background: var(--color-bg-subtle); "
        "border-bottom: 1px solid var(--color-rule); "
        "font-family: var(--font-mono); font-size: var(--text-sm);"
    ).props('data-testid="breadcrumb"') as container:
        if not segments:
            ui.label("(no selection)").style("color: var(--color-muted);")
            return container
        for idx, seg in enumerate(segments):
            if idx > 0:
                ui.label("/").style("color: var(--color-muted); margin: 0 4px;")
            label = ui.label(seg.label).props(
                f'data-testid="breadcrumb-segment" data-node-id="{seg.node_id}"'
            ).style("cursor: pointer; color: var(--color-link);")
            if on_navigate is not None:
                label.on("click", lambda _evt, nid=seg.node_id: on_navigate(nid))
    return container
