"""Canonical notification helpers (Frontend Spec §2.2).

This module is the **only** module in the codebase that calls NiceGUI's
``ui.notify``. The pre-commit hook ``no-direct-ui-notify`` (see
``.pre-commit-config.yaml``) enforces this rule across the package; an
additional unit test scans for stray ``ui.notify(`` calls in
``exlab_wizard/ui/``.

Surfaces:

* :func:`notify_success` / :func:`notify_info` / :func:`notify_warning` /
  :func:`notify_error` -- toasts (Frontend §2.2.2). 4 s for info / success,
  8 s for warning / error, extended to 12 s when an action is attached.
* :func:`notify_field_error` / :func:`notify_form_error` -- inline messages
  (Frontend §2.2.4). Stored in module-level registries so wizards can pull
  them per-field at render time.
* :func:`show_banner` / :func:`clear_banner` -- persistent banners
  (Frontend §2.2.3). The five-banner closed set is enforced via
  :class:`BannerId`. Stacking is capped at 2 simultaneous banners; a 3rd
  collapses to "...and N more issues".
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from exlab_wizard.logging import get_logger

_log = get_logger(__name__)


class Severity(StrEnum):
    """Banner severity (Frontend §2.2.3)."""

    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    DANGER = "danger"


class BannerId(StrEnum):
    """Closed-set banner triggers (Frontend §2.2.3).

    Adding a new banner is a deliberate spec change: update §2.2.3 and add
    a value here. Unknown ids are rejected at runtime in :func:`show_banner`.
    """

    SETUP_INCOMPLETE = "setup_incomplete"
    SYNC_BLOCKED_ON_SUCCESS_CARD = "sync_blocked_on_success_card"
    LIMS_UNREACHABLE = "lims_unreachable"
    NAS_UNREACHABLE = "nas_unreachable"
    RECONNECTING = "reconnecting"


class ContainerId(StrEnum):
    """Banner placement scopes (Frontend §2.2.3)."""

    GLOBAL = "global"
    WIZARD = "wizard"
    SETTINGS = "settings"


@dataclass(frozen=True)
class ActionSpec:
    """A single action affordance attached to a notification.

    At most one action per toast (Frontend §2.2.2); multi-action requirements
    escalate to a modal.
    """

    label: str
    on_click: Callable[[], None]


# Toast durations in milliseconds (Frontend §2.2.2).
_DURATION_INFO_SUCCESS_MS = 4000
_DURATION_WARNING_ERROR_MS = 8000
_DURATION_WITH_ACTION_MS = 12000

# Banner stacking cap (Frontend §2.2.3).
_BANNER_STACK_MAX = 2


# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------

# Per-banner-id record. Includes severity, message, optional action, and
# container id so the consumer can re-render anywhere.
_active_banners: dict[BannerId, dict[str, object]] = {}

# Field-level / form-level inline error registries. Wizards consult these on
# render to decide whether to draw the error treatment under each field.
_field_errors: dict[str, str] = {}
_form_errors: dict[str, str] = {}


# ---------------------------------------------------------------------------
# Toast helpers
# ---------------------------------------------------------------------------


def _emit_toast(
    *,
    message: str,
    nicegui_type: str,
    severity: Severity,
    action: ActionSpec | None,
) -> None:
    """Lower-level wrapper around ``ui.notify``.

    Calls into NiceGUI lazily to avoid hard-failing if the helper is invoked
    from a context that isn't bound to a NiceGUI app (for example, unit tests
    that import the module to assert the API shape).
    """

    if action is not None:
        timeout_ms = _DURATION_WITH_ACTION_MS
    elif severity in (Severity.WARNING, Severity.DANGER):
        timeout_ms = _DURATION_WARNING_ERROR_MS
    else:
        timeout_ms = _DURATION_INFO_SUCCESS_MS

    actions: list[dict[str, object]] | None = None
    if action is not None:
        actions = [
            {
                "label": action.label,
                "color": "white",
                "handler": action.on_click,
            }
        ]

    _log.info(
        "toast",
        extra={
            "event": "ui.toast",
            "severity": severity.value,
            "toast_message": message,
            "has_action": action is not None,
            "timeout_ms": timeout_ms,
        },
    )

    try:
        from nicegui import ui
    except Exception:
        return

    kwargs: dict[str, object] = {
        "type": nicegui_type,
        "position": "bottom-right",
        "timeout": timeout_ms,
        "close_button": True,
    }
    if actions is not None:
        kwargs["actions"] = actions

    ui.notify(message, **kwargs)


def notify_success(message: str, *, action: ActionSpec | None = None) -> None:
    """Show a success toast (Frontend §2.2.2).

    4 s default duration; 12 s when an action is attached.
    """

    _emit_toast(
        message=message,
        nicegui_type="positive",
        severity=Severity.SUCCESS,
        action=action,
    )


def notify_info(message: str, *, action: ActionSpec | None = None) -> None:
    """Show an info toast (Frontend §2.2.2)."""

    _emit_toast(
        message=message,
        nicegui_type="info",
        severity=Severity.INFO,
        action=action,
    )


def notify_warning(message: str, *, action: ActionSpec | None = None) -> None:
    """Show a warning toast (Frontend §2.2.2).

    8 s default duration; 12 s when an action is attached.
    """

    _emit_toast(
        message=message,
        nicegui_type="warning",
        severity=Severity.WARNING,
        action=action,
    )


def notify_error(message: str, *, action: ActionSpec | None = None) -> None:
    """Show an error toast (Frontend §2.2.2).

    8 s default duration; 12 s when an action is attached.
    """

    _emit_toast(
        message=message,
        nicegui_type="negative",
        severity=Severity.DANGER,
        action=action,
    )


# ---------------------------------------------------------------------------
# Inline validation
# ---------------------------------------------------------------------------


def notify_field_error(field_id: str, message: str) -> None:
    """Register a field-level inline error (Frontend §2.2.4)."""

    _field_errors[field_id] = message
    _log.debug(
        "field_error_set",
        extra={"event": "ui.inline.field", "field_id": field_id, "field_message": message},
    )


def notify_form_error(form_id: str, message: str) -> None:
    """Register a form-level inline error (Frontend §2.2.4)."""

    _form_errors[form_id] = message
    _log.debug(
        "form_error_set",
        extra={"event": "ui.inline.form", "form_id": form_id, "form_message": message},
    )


def clear_field_error(field_id: str) -> None:
    """Clear a previously registered field-level error."""

    _field_errors.pop(field_id, None)


def clear_form_errors(form_id: str) -> None:
    """Clear all form-level errors registered against ``form_id``."""

    _form_errors.pop(form_id, None)


def get_field_error(field_id: str) -> str | None:
    """Return the registered field-level error for ``field_id`` or ``None``."""

    return _field_errors.get(field_id)


def get_form_error(form_id: str) -> str | None:
    """Return the registered form-level error for ``form_id`` or ``None``."""

    return _form_errors.get(form_id)


# ---------------------------------------------------------------------------
# Banners
# ---------------------------------------------------------------------------


def show_banner(
    banner_id: BannerId,
    *,
    container: ContainerId,
    severity: Severity,
    message: str,
    action: ActionSpec | None = None,
    dismissible: bool = True,
) -> None:
    """Activate a banner from the closed §2.2.3 set.

    Raises:
        ValueError: when ``banner_id`` is not a member of :class:`BannerId`.
    """

    if not isinstance(banner_id, BannerId):
        raise ValueError(
            f"unknown banner id {banner_id!r}: only the §2.2.3 closed set is permitted",
        )

    _active_banners[banner_id] = {
        "container": container,
        "severity": severity,
        "message": message,
        "action": action,
        "dismissible": dismissible,
    }

    _log.info(
        "banner_shown",
        extra={
            "event": "ui.banner.shown",
            "banner_id": banner_id.value,
            "container": container.value,
            "severity": severity.value,
            "stack_size": len(_active_banners),
        },
    )


def clear_banner(banner_id: BannerId) -> None:
    """Deactivate a banner if it was active."""

    _active_banners.pop(banner_id, None)


def list_active_banners(
    container: ContainerId | None = None,
) -> list[tuple[BannerId, dict[str, object]]]:
    """Return active banners, optionally filtered by container.

    Banners are returned in registration order; banner-stack rendering rules
    (max 2 visible, 3rd+ collapses to *"...and N more issues"*) are applied
    by the consumer (typically the ``banner_stack`` component).
    """

    items = list(_active_banners.items())
    if container is not None:
        items = [(bid, rec) for bid, rec in items if rec["container"] == container]
    return items


def banner_overflow_count(container: ContainerId | None = None) -> int:
    """How many banners exceed the stacking cap of 2 (Frontend §2.2.3)."""

    visible = list_active_banners(container=container)
    return max(0, len(visible) - _BANNER_STACK_MAX)


def reset_for_tests() -> None:
    """Clear all module-level state. Test fixtures only."""

    _active_banners.clear()
    _field_errors.clear()
    _form_errors.clear()
