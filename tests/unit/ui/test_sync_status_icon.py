from __future__ import annotations

import pytest

from exlab_wizard.ui.components.sync_status_icon import (
    FileSyncView,
    file_sync_view,
    sync_pair_props,
    sync_rollup_icon_props,
)

SYNC_LOCAL = "/assets/sync_local.svg"
SYNC_NAS = "/assets/sync_nas.svg"


@pytest.mark.parametrize(
    "status, expected",
    [
        ("synced", FileSyncView.SYNCED),
        ("acquiring", FileSyncView.LOCAL_ONLY),
        ("syncing", FileSyncView.LOCAL_ONLY),
        ("on_nas", FileSyncView.ON_NAS),
        ("cleaned", FileSyncView.ON_NAS),
        ("cleared", FileSyncView.ON_NAS),
        ("upload_failed", FileSyncView.UPLOAD_FAILED),
        ("failed", FileSyncView.UPLOAD_FAILED),
        ("blocked", FileSyncView.BLOCKED),
        ("blocked_by_validation", FileSyncView.BLOCKED),
        ("missing", FileSyncView.MISSING),
        (None, FileSyncView.NONE),
        ("", FileSyncView.NONE),
        ("bogus", FileSyncView.NONE),
    ],
)
def test_file_sync_view_mapping(status, expected):
    assert file_sync_view(status) is expected


def test_pair_props_local_only_blue_local_gray_nas():
    p = sync_pair_props(FileSyncView.LOCAL_ONLY)
    assert p["local"]["svg"] == SYNC_LOCAL
    assert p["local"]["bg_var"] == "--color-sync-local"
    assert p["nas"]["svg"] == SYNC_NAS
    assert p["nas"]["bg_var"] == "--color-sync-absent"
    assert p["nas"]["faded"] is True


def test_pair_props_synced_faded_blue_local_green_nas():
    p = sync_pair_props(FileSyncView.SYNCED)
    assert p["local"]["bg_var"] == "--color-sync-cached"
    assert p["nas"]["bg_var"] == "--color-sync-safe"


def test_pair_props_on_nas_gray_local_green_nas():
    p = sync_pair_props(FileSyncView.ON_NAS)
    assert p["local"]["bg_var"] == "--color-sync-absent"
    assert p["nas"]["bg_var"] == "--color-sync-safe"


def test_pair_props_upload_failed_red_nas_badge():
    p = sync_pair_props(FileSyncView.UPLOAD_FAILED)
    assert p["local"]["bg_var"] == "--color-sync-local"
    assert p["nas"]["bg_var"] == "--color-sync-problem"
    assert p["nas"]["badge"] == "✕"


def test_pair_props_blocked_amber_nas_badge():
    p = sync_pair_props(FileSyncView.BLOCKED)
    assert p["nas"]["bg_var"] == "--color-sync-held"
    assert p["nas"]["badge"] == "!"


def test_pair_props_missing_red_both_badges():
    p = sync_pair_props(FileSyncView.MISSING)
    assert p["local"]["bg_var"] == "--color-sync-problem"
    assert p["nas"]["bg_var"] == "--color-sync-problem"
    assert p["local"]["badge"] == "✕" and p["nas"]["badge"] == "✕"


def test_pair_props_none_returns_none():
    assert sync_pair_props(FileSyncView.NONE) is None


@pytest.mark.parametrize(
    "view, svg, bg, badge",
    [
        (FileSyncView.SYNCED, SYNC_NAS, "--color-sync-safe", ""),
        (FileSyncView.ON_NAS, SYNC_NAS, "--color-sync-safe", ""),
        (FileSyncView.LOCAL_ONLY, SYNC_LOCAL, "--color-sync-local", ""),
        (FileSyncView.BLOCKED, SYNC_NAS, "--color-sync-held", "!"),
        (FileSyncView.UPLOAD_FAILED, SYNC_NAS, "--color-sync-problem", "✕"),
        (FileSyncView.MISSING, SYNC_NAS, "--color-sync-problem", "✕"),
    ],
)
def test_rollup_icon_props(view, svg, bg, badge):
    p = sync_rollup_icon_props(view)
    assert p["svg"] == svg
    assert p["bg_var"] == bg
    assert p["badge"] == badge


def test_rollup_icon_props_none():
    assert sync_rollup_icon_props(FileSyncView.NONE) is None
