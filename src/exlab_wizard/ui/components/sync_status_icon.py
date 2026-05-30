"""Two-icon sync-presence model + renderers (2026-05-30 design).

Each file shows a fixed **local-left / NAS-right** icon pair; runs and folders
show a single colour-coded rollup icon. The backend stays the state authority
and emits a discriminator string; this module is a pure presentation map:
``file_sync_view`` collapses the string to a :class:`FileSyncView`, then
``sync_pair_props`` / ``sync_rollup_icon_props`` map the view to icon + colour
props. Colour language: **blue = here · green = safe on NAS · gray = absent ·
red = problem · amber = held.** NiceGUI renderers (``sync_pair_icons`` /
``sync_rollup_icon``) live below the pure layer and import NiceGUI lazily.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Final

SYNC_LOCAL_SVG: Final[str] = "/assets/sync_local.svg"
SYNC_NAS_SVG: Final[str] = "/assets/sync_nas.svg"


class FileSyncView(StrEnum):
    """UI-only collapsed view of a file/run's sync state (presentation, not wire)."""

    LOCAL_ONLY = "local_only"
    SYNCED = "synced"
    ON_NAS = "on_nas"
    UPLOAD_FAILED = "upload_failed"
    BLOCKED = "blocked"
    MISSING = "missing"
    NONE = "none"


# Backend discriminator string -> view. Backend is the state authority; the UI
# only maps. Unknown / None -> NONE (render nothing).
_STATUS_TO_VIEW: Final[dict[str, FileSyncView]] = {
    "synced": FileSyncView.SYNCED,
    "acquiring": FileSyncView.LOCAL_ONLY,
    "syncing": FileSyncView.LOCAL_ONLY,
    "pending": FileSyncView.LOCAL_ONLY,
    "local_only": FileSyncView.LOCAL_ONLY,
    "on_nas": FileSyncView.ON_NAS,
    "cleaned": FileSyncView.ON_NAS,
    "cleared": FileSyncView.ON_NAS,
    "upload_failed": FileSyncView.UPLOAD_FAILED,
    "failed": FileSyncView.UPLOAD_FAILED,
    "blocked": FileSyncView.BLOCKED,
    "blocked_by_validation": FileSyncView.BLOCKED,
    "missing": FileSyncView.MISSING,
}


def file_sync_view(status: str | None) -> FileSyncView:
    """Map a backend sync discriminator string to a :class:`FileSyncView`."""
    if not status:
        return FileSyncView.NONE
    return _STATUS_TO_VIEW.get(str(status), FileSyncView.NONE)


# Per-view two-icon props. Each cell: svg, bg_var, badge, tooltip, faded.
_PAIR_PROPS: Final[dict[FileSyncView, dict[str, dict[str, Any]]]] = {
    FileSyncView.LOCAL_ONLY: {
        "local": {
            "svg": SYNC_LOCAL_SVG,
            "bg_var": "--color-sync-local",
            "badge": "",
            "tooltip": "Stored locally — not backed up yet",
            "faded": False,
        },
        "nas": {
            "svg": SYNC_NAS_SVG,
            "bg_var": "--color-sync-absent",
            "badge": "",
            "tooltip": "Not on the NAS yet",
            "faded": True,
        },
    },
    FileSyncView.SYNCED: {
        "local": {
            "svg": SYNC_LOCAL_SVG,
            "bg_var": "--color-sync-cached",
            "badge": "",
            "tooltip": "Local cache — safe to clear (backed up)",
            "faded": False,
        },
        "nas": {
            "svg": SYNC_NAS_SVG,
            "bg_var": "--color-sync-safe",
            "badge": "",
            "tooltip": "Backed up on the NAS",
            "faded": False,
        },
    },
    FileSyncView.ON_NAS: {
        "local": {
            "svg": SYNC_LOCAL_SVG,
            "bg_var": "--color-sync-absent",
            "badge": "",
            "tooltip": "Local copy reclaimed",
            "faded": True,
        },
        "nas": {
            "svg": SYNC_NAS_SVG,
            "bg_var": "--color-sync-safe",
            "badge": "",
            "tooltip": "Backed up on the NAS",
            "faded": False,
        },
    },
    FileSyncView.UPLOAD_FAILED: {
        "local": {
            "svg": SYNC_LOCAL_SVG,
            "bg_var": "--color-sync-local",
            "badge": "",
            "tooltip": "Stored locally — safe",
            "faded": False,
        },
        "nas": {
            "svg": SYNC_NAS_SVG,
            "bg_var": "--color-sync-problem",
            "badge": "✕",
            "tooltip": "Upload to NAS failed",
            "faded": False,
        },
    },
    FileSyncView.BLOCKED: {
        "local": {
            "svg": SYNC_LOCAL_SVG,
            "bg_var": "--color-sync-local",
            "badge": "",
            "tooltip": "Stored locally — safe",
            "faded": False,
        },
        "nas": {
            "svg": SYNC_NAS_SVG,
            "bg_var": "--color-sync-held",
            "badge": "!",
            "badge_bg": "--color-sync-held",
            "tooltip": "Upload held by a validation finding",
            "faded": False,
        },
    },
    FileSyncView.MISSING: {
        "local": {
            "svg": SYNC_LOCAL_SVG,
            "bg_var": "--color-sync-problem",
            "badge": "✕",
            "tooltip": "Missing — not found locally",
            "faded": False,
        },
        "nas": {
            "svg": SYNC_NAS_SVG,
            "bg_var": "--color-sync-problem",
            "badge": "✕",
            "tooltip": "Missing — not on the NAS",
            "faded": False,
        },
    },
}


def sync_pair_props(view: FileSyncView) -> dict[str, dict[str, Any]] | None:
    """Return ``{"local": cell, "nas": cell}`` for a file's two-icon pair, or None."""
    props = _PAIR_PROPS.get(view)
    if props is None:
        return None
    # Return a deep-ish copy so callers can't mutate the table.
    return {side: dict(cell) for side, cell in props.items()}


# Per-view single rollup icon (runs / folders): svg, bg_var, badge, tooltip.
_ROLLUP_PROPS: Final[dict[FileSyncView, dict[str, str]]] = {
    FileSyncView.SYNCED: {
        "svg": SYNC_NAS_SVG,
        "bg_var": "--color-sync-safe",
        "badge": "",
        "tooltip": "Fully backed up on the NAS",
    },
    FileSyncView.ON_NAS: {
        "svg": SYNC_NAS_SVG,
        "bg_var": "--color-sync-safe",
        "badge": "",
        "tooltip": "Fully backed up on the NAS",
    },
    FileSyncView.LOCAL_ONLY: {
        "svg": SYNC_LOCAL_SVG,
        "bg_var": "--color-sync-local",
        "badge": "",
        "tooltip": "Not fully synced — local files remain",
    },
    FileSyncView.BLOCKED: {
        "svg": SYNC_NAS_SVG,
        "bg_var": "--color-sync-held",
        "badge": "!",
        "badge_bg": "--color-sync-held",
        "tooltip": "Sync held by a validation finding",
    },
    FileSyncView.UPLOAD_FAILED: {
        "svg": SYNC_NAS_SVG,
        "bg_var": "--color-sync-problem",
        "badge": "✕",
        "tooltip": "Sync error",
    },
    FileSyncView.MISSING: {
        "svg": SYNC_NAS_SVG,
        "bg_var": "--color-sync-problem",
        "badge": "✕",
        "tooltip": "Sync error",
    },
}


def sync_rollup_icon_props(view: FileSyncView) -> dict[str, str] | None:
    """Return single-icon props for a run/folder rollup, or None for NONE."""
    props = _ROLLUP_PROPS.get(view)
    return dict(props) if props is not None else None


def _icon_cell(cell: dict[str, Any], *, side: str) -> None:  # pragma: no cover -- NiceGUI render
    from nicegui import ui

    box = (
        ui.element("span")
        .props(f'data-sync-cell="{side}" data-sync-bg="{cell["bg_var"]}"')
        .style(
            f"position: relative; display: inline-flex; align-items: center; "
            f"justify-content: center; width: 1.5rem; height: 1.5rem; "
            f"border-radius: var(--radius-sm); background: var({cell['bg_var']});"
        )
        .tooltip(cell["tooltip"])
    )
    with box:
        opacity = "0.45" if cell.get("faded") else "1"
        ui.element("img").props(f'src="{cell["svg"]}" alt="{cell["tooltip"]}"').style(
            f"width: 1rem; height: 1rem; opacity: {opacity};"
        )
        if cell["badge"]:
            # Badge dot matches the cell's alert hue: red for a problem, amber
            # for a held cell -- never red-on-amber (the white glyph stays legible
            # on both). Defaults to red for problem cells that omit the key.
            badge_bg = cell.get("badge_bg", "--color-sync-problem")
            ui.label(cell["badge"]).props('data-sync-badge="true"').style(
                "position: absolute; top: -3px; right: -3px; font-size: 0.6rem; "
                "line-height: 1; font-weight: 700; color: var(--color-surface); "
                f"background: var({badge_bg}); border-radius: 50%; "
                "width: 0.8rem; height: 0.8rem; display: flex; align-items: center; "
                "justify-content: center;"
            )


def sync_pair_icons(view: FileSyncView) -> Any:  # pragma: no cover -- NiceGUI render
    """Render the per-file two-icon (local + NAS) row, or nothing for NONE."""
    props = sync_pair_props(view)
    try:
        from nicegui import ui
    except Exception:
        return props
    if props is None:
        return ui.element("span").props('data-sync-view="none"')
    row = (
        ui.row()
        .classes("items-center")
        .props(f'data-sync-view="{view.value}"')
        .style("gap: 0.3rem;")
    )
    with row:
        _icon_cell(props["local"], side="local")
        _icon_cell(props["nas"], side="nas")
    return row


def sync_rollup_icon(view: FileSyncView) -> Any:  # pragma: no cover -- NiceGUI render
    """Render the single run/folder rollup icon, or nothing for NONE."""
    props = sync_rollup_icon_props(view)
    try:
        from nicegui import ui
    except Exception:
        return props
    if props is None:
        return ui.element("span").props('data-sync-view="none"')
    cell = {**props, "faded": False}
    row = ui.row().classes("items-center").props(f'data-sync-view="{view.value}"')
    with row:
        _icon_cell(cell, side="rollup")
    return row


def sync_legend_entries() -> list[dict[str, str]]:
    """Return legend rows (one per visible view) for the Files-header popover."""
    return [
        {"view": v.value, "tooltip": _ROLLUP_PROPS.get(v, {}).get("tooltip", "")}
        for v in (
            FileSyncView.LOCAL_ONLY,
            FileSyncView.SYNCED,
            FileSyncView.ON_NAS,
            FileSyncView.UPLOAD_FAILED,
            FileSyncView.BLOCKED,
            FileSyncView.MISSING,
        )
    ]
