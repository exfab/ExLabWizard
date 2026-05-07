"""Verify the literal values pinned in ``schema_versions``.

Each schema-version constant is committed by the design spec; bumping any of
these values is a coordinated migration. These tests guard against silent
edits.
"""

from __future__ import annotations

from exlab_wizard.constants import schema_versions


def test_creation_json_version_is_pinned() -> None:
    # Backend Spec §11.3 history table.
    assert schema_versions.CREATION_JSON_VERSION == "1.9"


def test_readme_fields_json_version_is_pinned() -> None:
    # Backend Spec §11.4.
    assert schema_versions.README_FIELDS_JSON_VERSION == "1.1"


def test_ingest_json_version_is_pinned() -> None:
    # Backend Spec §13.4.
    assert schema_versions.INGEST_JSON_VERSION == "1.1"


def test_equipment_json_version_is_pinned() -> None:
    # Backend Spec §11.4.1.
    assert schema_versions.EQUIPMENT_JSON_VERSION == "1.0"


def test_test_runs_json_version_is_pinned() -> None:
    # Backend Spec §11.4.2.
    assert schema_versions.TEST_RUNS_JSON_VERSION == "1.0"


def test_offline_catalogue_version_is_pinned() -> None:
    # Backend Spec §7.2.9.
    assert schema_versions.OFFLINE_CATALOGUE_VERSION == "1.0"


def test_readme_front_matter_schema_version_is_pinned() -> None:
    # Backend Spec §10.
    assert schema_versions.README_FRONT_MATTER_SCHEMA_VERSION == "1.1"


def test_all_schema_versions_are_strings() -> None:
    # Schema versions are always serialized as strings inside cache JSON, so
    # accidentally promoting them to floats would silently corrupt round-trips.
    for name in (
        "CREATION_JSON_VERSION",
        "README_FIELDS_JSON_VERSION",
        "INGEST_JSON_VERSION",
        "EQUIPMENT_JSON_VERSION",
        "TEST_RUNS_JSON_VERSION",
        "OFFLINE_CATALOGUE_VERSION",
        "README_FRONT_MATTER_SCHEMA_VERSION",
    ):
        assert isinstance(getattr(schema_versions, name), str), name


def test_schema_versions_re_exported_from_package() -> None:
    # The top-level package must re-export schema versions so callers can do
    # ``from exlab_wizard.constants import CREATION_JSON_VERSION``.
    from exlab_wizard import constants

    assert constants.CREATION_JSON_VERSION == "1.9"
    assert constants.README_FIELDS_JSON_VERSION == "1.1"
    assert constants.INGEST_JSON_VERSION == "1.1"
    assert constants.EQUIPMENT_JSON_VERSION == "1.0"
    assert constants.TEST_RUNS_JSON_VERSION == "1.0"
    assert constants.OFFLINE_CATALOGUE_VERSION == "1.0"
    assert constants.README_FRONT_MATTER_SCHEMA_VERSION == "1.1"
