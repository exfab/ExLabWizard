"""Tests for the msgspec.Struct cache-file types in ``exlab_wizard.api.schemas``.

The Struct types here are the only schema-validation surface for the
on-disk cache files (Backend Spec §11.3, §11.4). These tests pin:

* msgspec.json round-trip semantics (encode -> bytes -> decode -> Struct).
* Required-field enforcement at decode time.
* The override / tombstone serialization helpers (the spec requires
  ``revoked`` on every entry, and the helpers ensure that the omit-defaults
  Struct still emits the field).
"""

from __future__ import annotations

import pytest

from exlab_wizard.api.schemas import (
    CreationJson,
    EquipmentJson,
    IngestJson,
    LimsProjectBlock,
    OrchestratorBlock,
    OverrideEntry,
    PathsBlock,
    PluginApplied,
    PluginIsolation,
    ReadmeFieldsJson,
    TemplateBlock,
    TombstoneEntry,
    msgspec_json,
    override_entry_to_dict,
    parse_validation_override_entry,
    tombstone_entry_to_dict,
)

# Aliased import: pytest's collection rules treat a class whose name starts
# with ``Test`` as a test container and tries to instantiate it. The alias
# keeps the class available under a name pytest does not collect.
from exlab_wizard.api.schemas import TestRunsJson as RunsTestMarkerJson
from exlab_wizard.constants import (
    CreationLevel,
    IngestState,
    LIMSProjectSource,
    OrchestratorTransportType,
    PluginStatus,
    RunKind,
    RunScope,
    SyncStatus,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _minimal_creation_json(**overrides: object) -> CreationJson:
    """Build a minimal valid CreationJson at the current version for round-trip tests."""
    base = dict(
        schema_version="1.9",
        created_at="2026-04-17T14:32:00Z",
        created_by="asmith",
        level="run",
        run_kind="experimental",
        lims_project=LimsProjectBlock(
            uid="8c7e9d2f-1a4b-4e6c-9b3d-7f2a1e5d8c4b",
            short_id="PROJ-0042",
            name_at_creation="Cortex Q3 Pilot",
        ),
        template=TemplateBlock(
            name="confocal_run_v2",
            version="2.1",
            source_path="templates/confocal_run_v2",
            run_scope="both",
        ),
        variables={"project_name": "Cortex Q3 Pilot", "operator": "asmith"},
        paths=PathsBlock(
            local="/data/lab/CONFOCAL_01/PROJ-0042/Run_2026-04-17T14-32-00",
            nas="//nas01/lab/CONFOCAL_01/PROJ-0042/Run_2026-04-17T14-32-00",
        ),
    )
    base.update(overrides)
    return CreationJson(**base)


# ---------------------------------------------------------------------------
# CreationJson -- encode/decode round-trip
# ---------------------------------------------------------------------------


def test_creation_json_round_trips_through_msgspec() -> None:
    payload = _minimal_creation_json()
    encoded = msgspec_json.encode(payload)
    decoded = msgspec_json.decode(encoded, type=CreationJson)
    assert decoded == payload


def test_creation_json_round_trip_preserves_lims_project_subfields() -> None:
    payload = _minimal_creation_json(
        lims_project=LimsProjectBlock(
            uid="abc",
            short_id="PROJ-0001",
            name_at_creation="X",
            source="cache",
            cache_freshness_at_use="2026-04-17T13:00:00Z",
        ),
    )
    encoded = msgspec_json.encode(payload)
    decoded = msgspec_json.decode(encoded, type=CreationJson)
    assert decoded.lims_project.source == "cache"
    assert decoded.lims_project.cache_freshness_at_use == "2026-04-17T13:00:00Z"


def test_creation_json_round_trip_preserves_plugins_applied_with_isolation() -> None:
    payload = _minimal_creation_json(
        plugins_applied=[
            PluginApplied(
                plugin="xlsx_field_filler",
                version="0.3.1",
                files_affected=["metadata.xlsx"],
                status="success",
                isolation=PluginIsolation(duration_ms=412, exit_code=0, peak_memory_mb=38),
            ),
        ],
    )
    encoded = msgspec_json.encode(payload)
    decoded = msgspec_json.decode(encoded, type=CreationJson)
    assert len(decoded.plugins_applied) == 1
    plugin = decoded.plugins_applied[0]
    assert plugin.plugin == "xlsx_field_filler"
    assert plugin.isolation is not None
    assert plugin.isolation.duration_ms == 412


def test_creation_json_round_trip_preserves_orchestrator_block() -> None:
    payload = _minimal_creation_json(
        orchestrator=OrchestratorBlock(
            enabled=True, host="labpc-04", label="Lab Acquisition Station 01"
        ),
    )
    encoded = msgspec_json.encode(payload)
    decoded = msgspec_json.decode(encoded, type=CreationJson)
    assert decoded.orchestrator is not None
    assert decoded.orchestrator.enabled is True
    assert decoded.orchestrator.host == "labpc-04"


def test_creation_json_orchestrator_block_omitted_when_none() -> None:
    """Spec §11.3: orchestrator is *absent* (not null) in single-equipment mode."""
    payload = _minimal_creation_json()
    encoded = msgspec_json.encode(payload).decode()
    assert '"orchestrator"' not in encoded


def test_orchestrator_block_carries_relay_discovery_fields() -> None:
    """Redesign §3.3: pushed creation.json carries the equipment label +
    completeness signal so the orchestrator can auto-discover received
    equipment without a per-equipment registry of its own."""
    payload = _minimal_creation_json(
        orchestrator=OrchestratorBlock(
            enabled=True,
            host="labpc-04",
            label="Lab Acquisition Station 01",
            equipment_label="Confocal Microscope 1",
            completeness_signal="sentinel_file",
            sentinel_filename="acquisition_complete.flag",
        ),
    )
    encoded = msgspec_json.encode(payload)
    decoded = msgspec_json.decode(encoded, type=CreationJson)
    assert decoded.orchestrator is not None
    assert decoded.orchestrator.equipment_label == "Confocal Microscope 1"
    assert decoded.orchestrator.completeness_signal == "sentinel_file"
    assert decoded.orchestrator.sentinel_filename == "acquisition_complete.flag"
    assert decoded.orchestrator.manifest_filename is None


def test_orchestrator_block_relay_fields_default_to_empty_or_none() -> None:
    """Older creation.json files (no relay fields) decode cleanly."""
    payload = _minimal_creation_json(
        orchestrator=OrchestratorBlock(
            enabled=True, host="labpc-04", label="Lab Acquisition Station 01"
        ),
    )
    encoded = msgspec_json.encode(payload)
    decoded = msgspec_json.decode(encoded, type=CreationJson)
    assert decoded.orchestrator is not None
    assert decoded.orchestrator.equipment_label is None
    assert decoded.orchestrator.completeness_signal is None
    assert decoded.orchestrator.sentinel_filename is None
    assert decoded.orchestrator.manifest_filename is None


def test_creation_json_default_sync_status_is_pending() -> None:
    payload = _minimal_creation_json()
    assert payload.sync_status == "pending"


def test_creation_json_default_validation_overrides_is_empty_list() -> None:
    payload = _minimal_creation_json()
    assert payload.validation_overrides == []


# ---------------------------------------------------------------------------
# CreationJson -- required-field validation
# ---------------------------------------------------------------------------


def test_creation_json_missing_required_field_raises() -> None:
    import msgspec

    incomplete = b'{"schema_version": "1.9"}'
    with pytest.raises(msgspec.ValidationError):
        msgspec_json.decode(incomplete, type=CreationJson)


def test_creation_json_missing_lims_project_raises() -> None:
    """``lims_project`` is required at the project- and run-levels for v1.8."""
    bad = msgspec_json.encode(
        {
            "schema_version": "1.9",
            "created_at": "2026-04-17T14:32:00Z",
            "created_by": "asmith",
            "level": "run",
            "run_kind": "experimental",
            # lims_project intentionally absent
            "template": {
                "name": "x",
                "version": "1",
                "source_path": "x",
                "run_scope": "both",
            },
            "variables": {},
            "paths": {"local": "/x", "nas": "//y"},
        }
    )
    import msgspec

    with pytest.raises(msgspec.ValidationError):
        msgspec_json.decode(bad, type=CreationJson)


def test_creation_json_unknown_field_is_ignored_on_decode() -> None:
    """forbid_unknown_fields=False -- unknown fields are silently dropped at
    decode-time; the writer round-trips them via its raw-dict pass."""
    bad = msgspec_json.encode(
        {
            "schema_version": "1.9",
            "created_at": "2026-04-17T14:32:00Z",
            "created_by": "asmith",
            "level": "run",
            "run_kind": "experimental",
            "lims_project": {
                "uid": "x",
                "short_id": "PROJ-0001",
                "name_at_creation": "X",
            },
            "template": {
                "name": "x",
                "version": "1",
                "source_path": "x",
                "run_scope": "both",
            },
            "variables": {},
            "paths": {"local": "/x", "nas": "//y"},
            "future_field": {"hello": "world"},
        }
    )
    decoded = msgspec_json.decode(bad, type=CreationJson)
    # No exception raised; future_field is dropped from the typed view.
    assert decoded.schema_version == "1.9"


# ---------------------------------------------------------------------------
# OverrideEntry / TombstoneEntry helpers
# ---------------------------------------------------------------------------


def test_override_entry_to_dict_emits_revoked_field_explicitly() -> None:
    """Spec §11.3: ``revoked`` is required on every entry. ``omit_defaults``
    would have dropped it (default = False) -- the helper avoids that."""
    entry = OverrideEntry(
        id="aa",
        problem_class="unresolved_placeholder_token",
        operator="asmith",
        recorded_at="2026-04-18T09:14:22Z",
        reason="r",
    )
    out = override_entry_to_dict(entry)
    assert out["revoked"] is False
    assert out["problem_class"] == "unresolved_placeholder_token"


def test_tombstone_entry_to_dict_emits_revoked_field_explicitly() -> None:
    entry = TombstoneEntry(
        id="bb",
        revokes="aa",
        operator="asmith",
        recorded_at="2026-05-01T11:02:14Z",
        reason="r",
    )
    out = tombstone_entry_to_dict(entry)
    assert out["revoked"] is True
    assert out["revokes"] == "aa"


def test_parse_validation_override_entry_returns_override_for_revoked_false() -> None:
    out = parse_validation_override_entry(
        {
            "id": "aa",
            "problem_class": "unresolved_placeholder_token",
            "operator": "asmith",
            "recorded_at": "2026-04-18T09:14:22Z",
            "reason": "r",
            "revoked": False,
        }
    )
    assert isinstance(out, OverrideEntry)
    assert out.problem_class == "unresolved_placeholder_token"


def test_parse_validation_override_entry_returns_tombstone_for_revoked_true() -> None:
    out = parse_validation_override_entry(
        {
            "id": "bb",
            "revokes": "aa",
            "operator": "asmith",
            "recorded_at": "2026-05-01T11:02:14Z",
            "reason": "r",
            "revoked": True,
        }
    )
    assert isinstance(out, TombstoneEntry)
    assert out.revokes == "aa"


def test_creation_json_decodes_validation_overrides_as_list_of_dict() -> None:
    payload = _minimal_creation_json(
        validation_overrides=[
            override_entry_to_dict(
                OverrideEntry(
                    id="aa",
                    problem_class="leftover_jinja_marker",
                    operator="op",
                    recorded_at="2026-04-18T09:14:22Z",
                    reason="r",
                )
            ),
            tombstone_entry_to_dict(
                TombstoneEntry(
                    id="bb",
                    revokes="aa",
                    operator="op",
                    recorded_at="2026-05-01T11:02:14Z",
                    reason="r",
                )
            ),
        ]
    )
    encoded = msgspec_json.encode(payload)
    decoded = msgspec_json.decode(encoded, type=CreationJson)
    assert len(decoded.validation_overrides) == 2
    parsed_first = parse_validation_override_entry(decoded.validation_overrides[0])
    parsed_second = parse_validation_override_entry(decoded.validation_overrides[1])
    assert isinstance(parsed_first, OverrideEntry)
    assert isinstance(parsed_second, TombstoneEntry)


# ---------------------------------------------------------------------------
# ReadmeFieldsJson
# ---------------------------------------------------------------------------


def test_readme_fields_json_round_trips() -> None:
    payload = ReadmeFieldsJson(
        schema_version="1.1",
        generated_at="2026-04-17T14:32:05Z",
        core_fields={"label": "L", "operator": "asmith", "objective": "obj"},
        system_fields={
            "created": "2026-04-17T14:32:00Z",
            "created_by": "asmith",
            "equipment": {"id": "CONFOCAL_01", "label": "Confocal Microscope 1"},
        },
    )
    encoded = msgspec_json.encode(payload)
    decoded = msgspec_json.decode(encoded, type=ReadmeFieldsJson)
    assert decoded == payload


def test_readme_fields_json_optional_fields_default_to_empty() -> None:
    payload = ReadmeFieldsJson(
        schema_version="1.1",
        generated_at="2026-04-17T14:32:05Z",
        core_fields={"label": "L", "operator": "asmith", "objective": "obj"},
        system_fields={"created": "x"},
    )
    assert payload.template_fields == {}
    assert payload.config_fields == {}
    assert payload.custom_fields == []


def test_readme_fields_json_round_trip_preserves_custom_fields() -> None:
    payload = ReadmeFieldsJson(
        schema_version="1.1",
        generated_at="2026-04-17T14:32:05Z",
        core_fields={"label": "L", "operator": "asmith", "objective": "obj"},
        system_fields={"created": "x"},
        custom_fields=[
            {"label": "Collaborator", "value": "Dr. J. Lee"},
            {"label": "Expected duration (hr)", "value": "3.5"},
        ],
    )
    encoded = msgspec_json.encode(payload)
    decoded = msgspec_json.decode(encoded, type=ReadmeFieldsJson)
    assert decoded.custom_fields == [
        {"label": "Collaborator", "value": "Dr. J. Lee"},
        {"label": "Expected duration (hr)", "value": "3.5"},
    ]


def test_readme_fields_json_missing_required_field_raises() -> None:
    import msgspec

    with pytest.raises(msgspec.ValidationError):
        msgspec_json.decode(b'{"schema_version": "1.1"}', type=ReadmeFieldsJson)


# ---------------------------------------------------------------------------
# EquipmentJson
# ---------------------------------------------------------------------------


def test_equipment_json_round_trips() -> None:
    payload = EquipmentJson(
        schema_version="1.0",
        id="CONFOCAL_01",
        label="Confocal Microscope 1",
        configured_local_root="/data/lab",
        configured_nas_root="//nas01/lab",
        first_seen_at="2025-09-12T09:14:00Z",
        last_modified_at="2026-04-17T14:32:00Z",
    )
    encoded = msgspec_json.encode(payload)
    decoded = msgspec_json.decode(encoded, type=EquipmentJson)
    assert decoded == payload


def test_equipment_json_missing_required_field_raises() -> None:
    import msgspec

    with pytest.raises(msgspec.ValidationError):
        msgspec_json.decode(b'{"schema_version": "1.0"}', type=EquipmentJson)


# ---------------------------------------------------------------------------
# RunsTestMarkerJson
# ---------------------------------------------------------------------------


def test_test_runs_json_round_trips() -> None:
    payload = RunsTestMarkerJson(
        schema_version="1.0",
        created_at="2026-04-17T14:00:00Z",
        project="PROJ-0042",
        equipment="CONFOCAL_01",
    )
    encoded = msgspec_json.encode(payload)
    decoded = msgspec_json.decode(encoded, type=RunsTestMarkerJson)
    assert decoded == payload
    assert decoded.run_kind == "test"


def test_test_runs_json_missing_required_field_raises() -> None:
    import msgspec

    with pytest.raises(msgspec.ValidationError):
        msgspec_json.decode(b'{"schema_version": "1.0"}', type=RunsTestMarkerJson)


# ---------------------------------------------------------------------------
# StrEnum field round-trip: every (Struct, field) pair that switched from
# bare ``str`` to a canonical ``StrEnum`` must decode old-format JSON (raw
# string values) into the enum member, and re-encode to byte-identical JSON.
# Spec §11.3 / §13.4: the on-disk wire format is committed; the type-level
# refactor MUST NOT change the bytes.
# ---------------------------------------------------------------------------


def _ingest_json_payload(**overrides: object) -> bytes:
    """Build a minimal valid ingest.json JSON document for round-trip tests."""
    base: dict[str, object] = {
        "schema_version": "1.1",
        "project_name": "PROJ-0042",
        "equipment_id": "CONFOCAL_01",
        "run_kind": "experimental",
        "run_path": "/staging/Run_2026-04-17",
        "transport": "smb_mount",
        "current_state": "staging",
        "history": [],
    }
    base.update(overrides)
    return msgspec_json.encode(base)


def _creation_json_payload(**overrides: object) -> bytes:
    """Build a minimal valid creation.json JSON document for round-trip tests."""
    base: dict[str, object] = {
        "schema_version": "1.9",
        "created_at": "2026-04-17T14:32:00Z",
        "created_by": "asmith",
        "level": "run",
        "run_kind": "experimental",
        "lims_project": {
            "uid": "x",
            "short_id": "PROJ-0001",
            "name_at_creation": "X",
            "source": "live",
        },
        "template": {
            "name": "x",
            "version": "1",
            "source_path": "x",
            "run_scope": "both",
        },
        "variables": {},
        "paths": {"local": "/x", "nas": "//y"},
        "sync_status": "pending",
    }
    base.update(overrides)
    return msgspec_json.encode(base)


@pytest.mark.parametrize(
    ("struct_cls", "field", "raw_value", "enum_member", "build_payload"),
    [
        # CreationJson.level
        (
            CreationJson,
            "level",
            "project",
            CreationLevel.PROJECT,
            lambda: _creation_json_payload(level="project"),
        ),
        (
            CreationJson,
            "level",
            "run",
            CreationLevel.RUN,
            lambda: _creation_json_payload(level="run"),
        ),
        # CreationJson.run_kind
        (
            CreationJson,
            "run_kind",
            "experimental",
            RunKind.EXPERIMENTAL,
            lambda: _creation_json_payload(run_kind="experimental"),
        ),
        (
            CreationJson,
            "run_kind",
            "test",
            RunKind.TEST,
            lambda: _creation_json_payload(run_kind="test"),
        ),
        # CreationJson.sync_status (default = pending; round-trip a non-default value)
        (
            CreationJson,
            "sync_status",
            "synced",
            SyncStatus.SYNCED,
            lambda: _creation_json_payload(sync_status="synced"),
        ),
        (
            CreationJson,
            "sync_status",
            "blocked_by_validation",
            SyncStatus.BLOCKED_BY_VALIDATION,
            lambda: _creation_json_payload(sync_status="blocked_by_validation"),
        ),
        # IngestJson.run_kind
        (
            IngestJson,
            "run_kind",
            "experimental",
            RunKind.EXPERIMENTAL,
            lambda: _ingest_json_payload(run_kind="experimental"),
        ),
        (
            IngestJson,
            "run_kind",
            "test",
            RunKind.TEST,
            lambda: _ingest_json_payload(run_kind="test"),
        ),
        # IngestJson.transport
        (
            IngestJson,
            "transport",
            "smb_mount",
            OrchestratorTransportType.SMB_MOUNT,
            lambda: _ingest_json_payload(transport="smb_mount"),
        ),
        (
            IngestJson,
            "transport",
            "file_transfer",
            OrchestratorTransportType.FILE_TRANSFER,
            lambda: _ingest_json_payload(transport="file_transfer"),
        ),
        # IngestJson.current_state
        (
            IngestJson,
            "current_state",
            "staging",
            IngestState.STAGING,
            lambda: _ingest_json_payload(current_state="staging"),
        ),
        (
            IngestJson,
            "current_state",
            "complete",
            IngestState.COMPLETE,
            lambda: _ingest_json_payload(current_state="complete"),
        ),
        (
            IngestJson,
            "current_state",
            "sync_queued",
            IngestState.SYNC_QUEUED,
            lambda: _ingest_json_payload(current_state="sync_queued"),
        ),
        (
            IngestJson,
            "current_state",
            "sync_verified",
            IngestState.SYNC_VERIFIED,
            lambda: _ingest_json_payload(current_state="sync_verified"),
        ),
        (
            IngestJson,
            "current_state",
            "cleared",
            IngestState.CLEARED,
            lambda: _ingest_json_payload(current_state="cleared"),
        ),
    ],
)
def test_strenum_field_round_trip_preserves_wire_format(
    struct_cls: type,
    field: str,
    raw_value: str,
    enum_member: object,
    build_payload: object,
) -> None:
    """Decode an old-format JSON document (bare string at ``field``) into the
    typed Struct; the field must be the canonical enum member, equal to the
    raw string, and re-encoding must place the same raw string back on the
    wire."""
    raw_bytes = build_payload()  # type: ignore[operator]
    decoded = msgspec_json.decode(raw_bytes, type=struct_cls)
    value = getattr(decoded, field)
    assert value is enum_member
    assert value == raw_value
    re_encoded = msgspec_json.encode(decoded)
    needle = f'"{field}":"{raw_value}"'.encode()
    assert needle in re_encoded


@pytest.mark.parametrize(
    ("field", "raw_value", "enum_member", "json_segment"),
    [
        # LimsProjectBlock.source
        (
            "source",
            "live",
            LIMSProjectSource.LIVE,
            b'"source":"live"',
        ),
        (
            "source",
            "cache",
            LIMSProjectSource.CACHE,
            b'"source":"cache"',
        ),
        (
            "source",
            "offline_catalogue",
            LIMSProjectSource.OFFLINE_CATALOGUE,
            b'"source":"offline_catalogue"',
        ),
    ],
)
def test_lims_project_block_source_round_trip(
    field: str, raw_value: str, enum_member: LIMSProjectSource, json_segment: bytes
) -> None:
    raw_bytes = msgspec_json.encode(
        {
            "uid": "x",
            "short_id": "PROJ-0001",
            "name_at_creation": "X",
            "source": raw_value,
        }
    )
    decoded = msgspec_json.decode(raw_bytes, type=LimsProjectBlock)
    assert decoded.source is enum_member
    assert decoded.source == raw_value
    re_encoded = msgspec_json.encode(decoded)
    if enum_member is LIMSProjectSource.LIVE:
        # ``LIVE`` is the declared default; ``omit_defaults=True`` drops it
        # from the encoded bytes. The default still encodes to the historical
        # wire form: any non-default LimsProjectBlock includes ``source``.
        assert json_segment not in re_encoded
    else:
        assert json_segment in re_encoded


@pytest.mark.parametrize(
    ("raw_value", "enum_member"),
    [
        ("experimental", RunScope.EXPERIMENTAL),
        ("test", RunScope.TEST),
        ("both", RunScope.BOTH),
    ],
)
def test_template_block_run_scope_round_trip(raw_value: str, enum_member: RunScope) -> None:
    raw_bytes = msgspec_json.encode(
        {
            "name": "x",
            "version": "1",
            "source_path": "x",
            "run_scope": raw_value,
        }
    )
    decoded = msgspec_json.decode(raw_bytes, type=TemplateBlock)
    assert decoded.run_scope is enum_member
    assert decoded.run_scope == raw_value
    re_encoded = msgspec_json.encode(decoded)
    assert f'"run_scope":"{raw_value}"'.encode() in re_encoded


@pytest.mark.parametrize(
    ("raw_value", "enum_member"),
    [
        ("success", PluginStatus.SUCCESS),
        ("failed", PluginStatus.FAILED),
        ("skipped", PluginStatus.SKIPPED),
        ("timeout", PluginStatus.TIMEOUT),
        ("policy_violation", PluginStatus.POLICY_VIOLATION),
    ],
)
def test_plugin_applied_status_round_trip(raw_value: str, enum_member: PluginStatus) -> None:
    raw_bytes = msgspec_json.encode(
        {
            "plugin": "p",
            "version": "1",
            "files_affected": ["x"],
            "status": raw_value,
        }
    )
    decoded = msgspec_json.decode(raw_bytes, type=PluginApplied)
    assert decoded.status is enum_member
    assert decoded.status == raw_value
    re_encoded = msgspec_json.encode(decoded)
    assert f'"status":"{raw_value}"'.encode() in re_encoded


def test_test_runs_json_default_run_kind_serializes_as_test() -> None:
    """The ``run_kind`` default switched from bare ``"test"`` to
    ``RunKind.TEST``; ``omit_defaults=True`` should still drop it from the
    encoded bytes (and decoding a payload without the field must yield
    ``RunKind.TEST``)."""
    payload = RunsTestMarkerJson(
        schema_version="1.0",
        created_at="2026-04-17T14:00:00Z",
        project="PROJ-0042",
        equipment="CONFOCAL_01",
    )
    encoded = msgspec_json.encode(payload)
    assert b'"run_kind"' not in encoded
    decoded = msgspec_json.decode(encoded, type=RunsTestMarkerJson)
    assert decoded.run_kind is RunKind.TEST


def test_test_runs_json_explicit_run_kind_round_trips() -> None:
    raw_bytes = msgspec_json.encode(
        {
            "schema_version": "1.0",
            "created_at": "2026-04-17T14:00:00Z",
            "project": "PROJ-0042",
            "equipment": "CONFOCAL_01",
            "run_kind": "experimental",
        }
    )
    decoded = msgspec_json.decode(raw_bytes, type=RunsTestMarkerJson)
    assert decoded.run_kind is RunKind.EXPERIMENTAL


def test_creation_json_default_sync_status_omitted_on_encode() -> None:
    """``SyncStatus.PENDING`` is the declared default; ``omit_defaults=True``
    drops it from encoded bytes, matching the pre-enum wire format."""
    payload = _minimal_creation_json()
    encoded = msgspec_json.encode(payload)
    assert b'"sync_status"' not in encoded
    decoded = msgspec_json.decode(encoded, type=CreationJson)
    assert decoded.sync_status is SyncStatus.PENDING


def test_lims_project_block_default_source_omitted_on_encode() -> None:
    """``LIMSProjectSource.LIVE`` is the declared default; ``omit_defaults=True``
    drops it from encoded bytes, matching the pre-enum wire format."""
    block = LimsProjectBlock(
        uid="x",
        short_id="PROJ-0001",
        name_at_creation="X",
    )
    encoded = msgspec_json.encode(block)
    assert b'"source"' not in encoded
    decoded = msgspec_json.decode(encoded, type=LimsProjectBlock)
    assert decoded.source is LIMSProjectSource.LIVE


# ---------------------------------------------------------------------------
# lims/schemas.py LIMSProject.status -- enum round-trip. Wire format is
# PascalCase per LIMS REST convention (Backend Spec §7.2). Lives here next
# to the other Phase E1 round-trip tests so a single test module pins every
# Struct.field that switched to a StrEnum.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw_value", "enum_member"),
    [
        ("Pending", "PENDING"),
        ("Active", "ACTIVE"),
        ("Completed", "COMPLETED"),
        ("Archived", "ARCHIVED"),
    ],
)
def test_lims_project_status_round_trip(raw_value: str, enum_member: str) -> None:
    from exlab_wizard.constants import LIMSProjectStatus
    from exlab_wizard.lims.schemas import LIMSProject

    raw_bytes = msgspec_json.encode(
        {
            "uid": "u",
            "short_id": "PROJ-0001",
            "name": "X",
            "status": raw_value,
            "owner": "asmith",
            "fetched_at": "2026-04-17T14:00:00Z",
        }
    )
    decoded = msgspec_json.decode(raw_bytes, type=LIMSProject)
    assert decoded.status is LIMSProjectStatus[enum_member]
    assert decoded.status == raw_value
    re_encoded = msgspec_json.encode(decoded)
    assert f'"status":"{raw_value}"'.encode() in re_encoded
