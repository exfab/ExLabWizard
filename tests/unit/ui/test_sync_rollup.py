from __future__ import annotations

from exlab_wizard.ui.components.sync_rollup import sync_rollup


def test_empty_is_none():
    assert sync_rollup([]) is None


def test_only_none_is_none():
    assert sync_rollup([None, "", "bogus"]) is None


def test_all_synced():
    assert sync_rollup(["synced", "synced"]) == "synced"


def test_on_nas_is_calmest():
    # on_nas/cleaned/cleared roll up to on_nas only when nothing louder present
    assert sync_rollup(["cleaned", "on_nas"]) == "on_nas"


def test_local_only_outranks_synced():
    assert sync_rollup(["synced", "acquiring"]) == "local_only"


def test_blocked_outranks_local_only():
    assert sync_rollup(["acquiring", "blocked"]) == "blocked"


def test_upload_failed_outranks_blocked():
    assert sync_rollup(["blocked", "failed"]) == "upload_failed"


def test_missing_is_worst():
    assert sync_rollup(["failed", "missing", "synced"]) == "missing"
