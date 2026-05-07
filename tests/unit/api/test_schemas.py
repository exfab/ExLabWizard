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
