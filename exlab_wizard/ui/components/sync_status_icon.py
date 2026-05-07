"""Sync-status icon component (Frontend Spec §3.2, §10.5.1).

Six distinct visual states with a fixed color mapping:

* ``pending``               -- ``--color-muted``
* ``retrying`` (with N/M)   -- ``--color-info``
* ``synced``                -- ``--color-success``
* ``failed``                -- ``--color-danger``
* ``blocked_by_validation`` -- ``--color-warning``
* ``override_active``       -- ``--color-info``

The component returns a dict suitable for a NiceGUI icon factory; the
layout (icon + optional ``(N/M)`` retry counter) is the caller's concern
so the icon can be embedded in a tree row, a detail-pane title bar, or a
staging-panel row identically.
"""

from __future__ import annotations

from typing import Any

from exlab_wizard.logging import get_logger

_log = get_logger(__name__)


# Backend §3 / §4 enum values for sync status.
STATUS_PENDING = "pending"
STATUS_RETRYING = "retrying"
STATUS_SYNCED = "synced"
STATUS_FAILED = "failed"
STATUS_BLOCKED = "blocked_by_validation"
STATUS_OVERRIDE = "override_active"


_STATUS_TO_PROPS: dict[str, dict[str, str]] = {
    STATUS_PENDING: {
        "icon_name": "schedule",
        "color_var": "--color-muted",
        "tooltip": "Queued for sync",
    },
    STATUS_RETRYING: {
        "icon_name": "history",
        "color_var": "--color-info",
        "tooltip": "Retrying with backoff",
    },
    STATUS_SYNCED: {
        "icon_name": "check_circle",
        "color_var": "--color-success",
        "tooltip": "Synced and verified at NAS",
    },
    STATUS_FAILED: {
        "icon_name": "error",
        "color_var": "--color-danger",
        "tooltip": "Sync failed; retry budget exhausted",
    },
    STATUS_BLOCKED: {
        "icon_name": "warning",
        "color_var": "--color-warning",
        "tooltip": "Hard-tier validation finding gates sync",
    },
    STATUS_OVERRIDE: {
        "icon_name": "lock_open",
        "color_var": "--color-info",
        "tooltip": "Sync allowed under operator override",
    },
}


def sync_status_props(
    status: str,
    *,
    retry_n: int | None = None,
    retry_m: int | None = None,
) -> dict[str, Any]:
    """Compute icon + tooltip + retry-counter for a sync status.

    The ``retry_n``/``retry_m`` annotations are rendered only when
    ``status == "retrying"`` (Frontend §10.5.1).
    """

    if status not in _STATUS_TO_PROPS:
        raise ValueError(
            f"unknown sync status {status!r}: must be one of {sorted(_STATUS_TO_PROPS)}",
        )
    base = dict(_STATUS_TO_PROPS[status])
    base["status"] = status
    if status == STATUS_RETRYING and retry_n is not None and retry_m is not None:
        base["retry_label"] = f"({retry_n}/{retry_m})"
        base["tooltip"] = f"Retry {retry_n} of {retry_m}, awaiting backoff"
    else:
        base["retry_label"] = ""
    return base


def sync_status_icon(
    status: str,
    *,
    retry_n: int | None = None,
    retry_m: int | None = None,
) -> Any:
    """Build a NiceGUI row containing the icon and optional retry counter."""

    props = sync_status_props(status, retry_n=retry_n, retry_m=retry_m)
    try:
        from nicegui import ui
    except Exception:
        return props

    row = ui.row().classes("items-center").style("gap: 0.25rem;")
    with row:
        ui.icon(props["icon_name"]).style(
            f"color: var({props['color_var']}); font-size: 1rem;"
        ).tooltip(props["tooltip"])
        if props["retry_label"]:
            ui.label(props["retry_label"]).style(
                "font-family: var(--font-mono); "
                "font-size: var(--text-xs); "
                f"color: var({props['color_var']});"
            )
    return row
