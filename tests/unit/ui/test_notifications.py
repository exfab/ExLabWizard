"""Unit tests for :mod:`exlab_wizard.ui.notifications`.

The helpers all delegate to NiceGUI's ``ui.notify``; the tests patch
``nicegui.ui.notify`` directly so the helpers can be called without an
active app context.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from exlab_wizard.ui import notifications
from exlab_wizard.ui.notifications import (
    ActionSpec,
    BannerId,
    ContainerId,
    Severity,
)


@pytest.fixture(autouse=True)
def _reset_state() -> Any:
    """Clear in-memory banner / inline state between tests."""

    notifications.reset_for_tests()
    yield
    notifications.reset_for_tests()


def test_notify_success_uses_positive_type() -> None:
    """``notify_success`` calls ``ui.notify(... type="positive")``."""

    with patch("nicegui.ui.notify") as mock_notify:
        notifications.notify_success("Saved.")
    mock_notify.assert_called_once()
    kwargs = mock_notify.call_args.kwargs
    assert kwargs["type"] == "positive"
    assert kwargs["position"] == "bottom-right"
    assert kwargs["timeout"] == 4000


def test_notify_info_default_timeout() -> None:
    """``notify_info`` uses the 4 s default."""

    with patch("nicegui.ui.notify") as mock_notify:
        notifications.notify_info("Heads up.")
    assert mock_notify.call_args.kwargs["timeout"] == 4000
    assert mock_notify.call_args.kwargs["type"] == "info"


def test_notify_warning_extended_timeout() -> None:
    """``notify_warning`` uses the 8 s default."""

    with patch("nicegui.ui.notify") as mock_notify:
        notifications.notify_warning("Heads up.")
    assert mock_notify.call_args.kwargs["timeout"] == 8000
    assert mock_notify.call_args.kwargs["type"] == "warning"


def test_notify_error_extended_timeout() -> None:
    """``notify_error`` uses the 8 s default."""

    with patch("nicegui.ui.notify") as mock_notify:
        notifications.notify_error("Boom.")
    assert mock_notify.call_args.kwargs["timeout"] == 8000
    assert mock_notify.call_args.kwargs["type"] == "negative"


def test_notify_with_action_extends_to_12_seconds() -> None:
    """When an action is attached, the timeout extends to 12 s."""

    spec = ActionSpec(label="Undo", on_click=lambda: None)
    with patch("nicegui.ui.notify") as mock_notify:
        notifications.notify_warning("Removed.", action=spec)
    kwargs = mock_notify.call_args.kwargs
    assert kwargs["timeout"] == 12000
    assert kwargs["actions"] == [
        {"label": "Undo", "color": "white", "handler": spec.on_click},
    ]


def test_show_banner_rejects_unknown_id() -> None:
    """An out-of-set banner id raises ``ValueError``."""

    with pytest.raises(ValueError):
        notifications.show_banner(
            "not_a_real_banner",  # type: ignore[arg-type]
            container=ContainerId.GLOBAL,
            severity=Severity.WARNING,
            message="x",
        )


def test_show_banner_records_active() -> None:
    """``show_banner`` records the banner under the right container."""

    notifications.show_banner(
        BannerId.SETUP_INCOMPLETE,
        container=ContainerId.GLOBAL,
        severity=Severity.WARNING,
        message="Configure Settings.",
    )
    items = notifications.list_active_banners()
    assert len(items) == 1
    assert items[0][0] is BannerId.SETUP_INCOMPLETE


def test_clear_banner_removes_record() -> None:
    """``clear_banner`` removes the active record."""

    notifications.show_banner(
        BannerId.LIMS_UNREACHABLE,
        container=ContainerId.WIZARD,
        severity=Severity.DANGER,
        message="LIMS down.",
    )
    notifications.clear_banner(BannerId.LIMS_UNREACHABLE)
    assert notifications.list_active_banners() == []


def test_banner_overflow_count_when_exceeding_two() -> None:
    """Frontend §2.2.3: max 2 banners; 3rd collapses into a count."""

    notifications.show_banner(
        BannerId.SETUP_INCOMPLETE,
        container=ContainerId.GLOBAL,
        severity=Severity.WARNING,
        message="a",
    )
    notifications.show_banner(
        BannerId.NAS_UNREACHABLE,
        container=ContainerId.GLOBAL,
        severity=Severity.DANGER,
        message="b",
    )
    notifications.show_banner(
        BannerId.LIMS_UNREACHABLE,
        container=ContainerId.GLOBAL,
        severity=Severity.DANGER,
        message="c",
    )
    assert notifications.banner_overflow_count() == 1


def test_field_errors_round_trip() -> None:
    """Inline field errors round-trip through register / get / clear."""

    notifications.notify_field_error("operator", "required")
    assert notifications.get_field_error("operator") == "required"
    notifications.clear_field_error("operator")
    assert notifications.get_field_error("operator") is None


def test_form_errors_round_trip() -> None:
    """Inline form errors round-trip through register / get / clear."""

    notifications.notify_form_error("readme", "fix the readme fields")
    assert notifications.get_form_error("readme") == "fix the readme fields"
    notifications.clear_form_errors("readme")
    assert notifications.get_form_error("readme") is None


def test_banner_id_closed_set_matches_spec() -> None:
    """Frontend §2.2.3: exactly five canonical banner ids."""

    assert {b.value for b in BannerId} == {
        "setup_incomplete",
        "sync_blocked_on_success_card",
        "lims_unreachable",
        "nas_unreachable",
        "reconnecting",
    }


def test_severity_closed_set_matches_spec() -> None:
    """Severity matches DESIGN.md alerts table."""

    assert {s.value for s in Severity} == {
        "info",
        "success",
        "warning",
        "danger",
    }


def test_container_id_closed_set_matches_spec() -> None:
    """ContainerId enumerates the three banner placement scopes."""

    assert {c.value for c in ContainerId} == {
        "global",
        "wizard",
        "settings",
    }


def test_action_spec_is_frozen() -> None:
    """``ActionSpec`` is immutable so callbacks can't be swapped post-hoc."""

    from dataclasses import FrozenInstanceError

    spec = ActionSpec(label="Undo", on_click=lambda: None)
    with pytest.raises(FrozenInstanceError):
        spec.label = "Other"  # type: ignore[misc]
