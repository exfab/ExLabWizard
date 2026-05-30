"""Unit tests for sync_status_icon tolerant mode (Redesign §4.5, OQ-5)."""

from __future__ import annotations

import pytest

from exlab_wizard.constants import SyncStatus
from exlab_wizard.ui.components.sync_status_icon import sync_status_props


def test_strict_default_still_raises_on_unknown() -> None:
    """The original contract is preserved: strict (default) raises."""
    with pytest.raises(ValueError, match="unknown sync status"):
        sync_status_props("bogus")


def test_strict_explicit_raises_on_unknown() -> None:
    with pytest.raises(ValueError, match="unknown sync status"):
        sync_status_props("bogus", strict=True)


def test_tolerant_mode_returns_neutral_for_unknown() -> None:
    """strict=False yields neutral props (muted dash) instead of raising."""
    props = sync_status_props("bogus", strict=False)
    assert props["icon_name"] == ""
    assert props["color_var"] == "--color-muted"
    assert props["tooltip"] == ""
    assert props["retry_label"] == ""
    assert props["status"] == "bogus"


def test_tolerant_mode_handles_none() -> None:
    """strict=False accepts None (optional per-file / per-folder status)."""
    props = sync_status_props(None, strict=False)
    assert props["icon_name"] == ""
    assert props["color_var"] == "--color-muted"
    assert props["status"] == ""


def test_tolerant_mode_still_returns_real_props_for_known() -> None:
    """A recognised status is unaffected by strict=False."""
    props = sync_status_props(SyncStatus.SYNCED, strict=False)
    assert props["icon_name"]
    assert props["status"] == SyncStatus.SYNCED.value


def test_neutral_and_normal_props_share_key_shape() -> None:
    """Both return paths expose the same keys so callers treat them alike."""
    normal = sync_status_props(SyncStatus.SYNCED)
    neutral = sync_status_props(None, strict=False)
    assert set(normal) == set(neutral)
