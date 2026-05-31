"""Unit tests for the main-page sync-status legend popover.

The legend lists each sync view as a two-icon (local + NAS) swatch + its
meaning so operators can decode the per-file / per-run sync icons
(two-icon sync-presence design, 2026-05-30).
"""

from __future__ import annotations

from exlab_wizard.ui.components.sync_status_icon import FileSyncView, sync_legend_entries


def test_legend_entries_have_view_and_tooltip() -> None:
    """Each legend entry exposes the view value + human tooltip the popover needs."""
    entries = sync_legend_entries()
    assert entries, "legend must list at least one sync state"
    for entry in entries:
        # ``view`` must be a real FileSyncView value the renderer can rehydrate.
        assert FileSyncView(entry["view"]) is not FileSyncView.NONE
        assert entry["tooltip"], "each legend entry needs a tooltip"


def test_legend_renders_without_error() -> None:
    """The legend renderer tolerates a no-NiceGUI context (returns cleanly)."""
    from exlab_wizard.ui.pages.main import _render_sync_legend

    # Outside a NiceGUI app context the helper must no-op without raising.
    _render_sync_legend()
    return None
