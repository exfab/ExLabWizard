from __future__ import annotations

from exlab_wizard.api.routers import browse


class _Rec:
    def __init__(self, verified: bool):
        self.verified_at = "2026-05-30T00:00:00Z" if verified else None
        self.keep_local = False


def test_no_record_on_disk_is_acquiring():
    assert browse._file_state_from_record(None, on_disk=True) == "acquiring"


def test_no_record_absent_is_none():
    assert browse._file_state_from_record(None, on_disk=False) is None


def test_verified_on_disk_is_synced():
    assert browse._file_state_from_record(_Rec(True), on_disk=True) == "synced"


def test_unverified_on_disk_is_syncing():
    assert browse._file_state_from_record(_Rec(False), on_disk=True) == "syncing"


def test_verified_absent_is_on_nas():
    assert browse._file_state_from_record(_Rec(True), on_disk=False) == "on_nas"


def test_unverified_absent_is_missing():
    # Previously None (silently dropped); now surfaced as a lost file.
    assert browse._file_state_from_record(_Rec(False), on_disk=False) == "missing"


def test_run_failed_makes_unverified_on_disk_upload_failed():
    assert (
        browse._file_state_from_record(_Rec(False), on_disk=True, run_failed=True)
        == "upload_failed"
    )


def test_run_blocked_makes_unverified_on_disk_blocked():
    assert browse._file_state_from_record(_Rec(False), on_disk=True, run_blocked=True) == "blocked"


def test_run_failed_does_not_override_synced():
    assert browse._file_state_from_record(_Rec(True), on_disk=True, run_failed=True) == "synced"
