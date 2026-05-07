"""Unit tests for ``exlab_wizard.api.events``."""

from __future__ import annotations

import json

import msgspec
import pytest

from exlab_wizard.api.events import (
    DeltaEvent,
    DoneEvent,
    FailedEvent,
    InputRequiredEvent,
    PhaseEvent,
    ProgressEvent,
    SnapshotEvent,
    WarningEvent,
    encode_event,
    event_from_dict,
)


def test_phase_event_round_trip() -> None:
    event = PhaseEvent(phase="rendering_template", at="2026-05-05T12:00:00Z")
    blob = encode_event(event)
    assert json.loads(blob) == {
        "kind": "phase",
        "phase": "rendering_template",
        "at": "2026-05-05T12:00:00Z",
    }


def test_progress_event_includes_kind() -> None:
    event = ProgressEvent(phase="running_plugins", current=2, total=4)
    decoded = json.loads(encode_event(event))
    assert decoded["kind"] == "progress"
    assert decoded["current"] == 2 and decoded["total"] == 4


def test_input_required_event_carries_fields() -> None:
    event = InputRequiredEvent(
        fields=[{"id": "operator_initials", "type": "string"}],
        reason="please confirm",
        plugin="xlsx_field_filler",
    )
    decoded = json.loads(encode_event(event))
    assert decoded["kind"] == "input_required"
    assert decoded["plugin"] == "xlsx_field_filler"


def test_warning_event_serializes() -> None:
    event = WarningEvent(phase="queueing_nas_sync", message="post-validate gate")
    decoded = json.loads(encode_event(event))
    assert decoded == {
        "kind": "warning",
        "phase": "queueing_nas_sync",
        "message": "post-validate gate",
    }


def test_done_event_carries_result() -> None:
    event = DoneEvent(result={"path": "/data/foo", "sync_status": "pending"})
    decoded = json.loads(encode_event(event))
    assert decoded["kind"] == "done"
    assert decoded["result"]["path"] == "/data/foo"


def test_failed_event_carries_error() -> None:
    event = FailedEvent(phase="rendering_template", error={"code": "validation_failed"})
    decoded = json.loads(encode_event(event))
    assert decoded["kind"] == "failed"
    assert decoded["error"]["code"] == "validation_failed"


def test_snapshot_event_lists_findings() -> None:
    event = SnapshotEvent(findings=[{"rule": "orphan", "tier": "soft"}], audit_at="2026-05-05T12:00:00Z")
    decoded = json.loads(encode_event(event))
    assert decoded["kind"] == "snapshot"
    assert len(decoded["findings"]) == 1


def test_delta_event_serializes_three_lists() -> None:
    event = DeltaEvent(added=[], removed=[], changed=[], audit_at="2026-05-05T12:00:00Z")
    decoded = json.loads(encode_event(event))
    assert decoded["kind"] == "delta"
    assert "added" in decoded and "removed" in decoded and "changed" in decoded


def test_event_from_dict_round_trips_each_kind() -> None:
    samples = [
        {"kind": "phase", "phase": "rendering_template", "at": "x"},
        {"kind": "progress", "phase": "x", "current": 1, "total": 2},
        {"kind": "input_required", "fields": [], "reason": "x", "plugin": "x"},
        {"kind": "warning", "phase": "x", "message": "x"},
        {"kind": "done", "result": {}},
        {"kind": "failed", "phase": "x", "error": {}},
        {"kind": "snapshot", "findings": [], "audit_at": "x"},
        {"kind": "delta", "added": [], "removed": [], "changed": [], "audit_at": "x"},
    ]
    for payload in samples:
        typed = event_from_dict(payload)
        # Re-encode and compare back to the original (msgspec adds the
        # tag field, so decode the encoded blob and verify shape).
        re_decoded = json.loads(msgspec.json.encode(typed))
        assert re_decoded["kind"] == payload["kind"]


def test_event_from_dict_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError):
        event_from_dict({"kind": "not_a_kind"})


def test_event_from_dict_rejects_missing_kind() -> None:
    with pytest.raises(ValueError):
        event_from_dict({})
