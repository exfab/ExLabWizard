"""Tests for the offline LIMS-project catalogue I/O. Backend Spec §7.2.9."""

from __future__ import annotations

import msgspec
import pytest

from exlab_wizard.constants import OFFLINE_CATALOGUE_VERSION
from exlab_wizard.errors import ConfigError
from exlab_wizard.lims.catalogue import OfflineCatalogue, read_catalogue, write_catalogue
from exlab_wizard.lims.schemas import LIMSProject


def _make_catalogue(*, endpoint: str = "http://lims.test/api/v1") -> OfflineCatalogue:
    return OfflineCatalogue(
        schema_version=OFFLINE_CATALOGUE_VERSION,
        produced_by="LAB_STATION_01",
        produced_at="2026-05-05T14:23:00Z",
        lims_endpoint=endpoint,
        projects=[
            LIMSProject(
                uid="uid-1",
                short_id="PROJ-0001",
                name="Cortex Q3 Pilot",
                description=None,
                status="Active",
                contact_name=None,
                owner="owner",
                metadata={"k": "v"},
                fetched_at="2026-05-05T14:23:00Z",
            )
        ],
    )


def test_round_trip_via_write_then_read(tmp_path) -> None:
    path = tmp_path / "cat.json"
    catalogue = _make_catalogue()
    write_catalogue(path, catalogue)
    parsed = read_catalogue(path, expected_endpoint=catalogue.lims_endpoint)
    assert parsed.schema_version == OFFLINE_CATALOGUE_VERSION
    assert parsed.produced_by == "LAB_STATION_01"
    assert parsed.lims_endpoint == catalogue.lims_endpoint
    assert len(parsed.projects) == 1
    assert parsed.projects[0].short_id == "PROJ-0001"


def test_read_missing_file_raises(tmp_path) -> None:
    with pytest.raises(ConfigError):
        read_catalogue(tmp_path / "absent.json", expected_endpoint="http://x")


def test_read_invalid_json_raises(tmp_path) -> None:
    path = tmp_path / "cat.json"
    path.write_bytes(b"not json")
    with pytest.raises(ConfigError):
        read_catalogue(path, expected_endpoint="http://x")


def test_read_non_object_raises(tmp_path) -> None:
    path = tmp_path / "cat.json"
    path.write_bytes(b"[1, 2, 3]")
    with pytest.raises(ConfigError):
        read_catalogue(path, expected_endpoint="http://x")


def test_schema_version_mismatch_raises(tmp_path) -> None:
    path = tmp_path / "cat.json"
    payload = {
        "schema_version": "9.9",
        "produced_by": "x",
        "produced_at": "now",
        "lims_endpoint": "http://lims.test/api/v1",
        "projects": [],
    }
    path.write_bytes(msgspec.json.encode(payload))
    with pytest.raises(ConfigError, match="schema_version"):
        read_catalogue(path, expected_endpoint="http://lims.test/api/v1")


def test_endpoint_mismatch_raises(tmp_path) -> None:
    path = tmp_path / "cat.json"
    catalogue = _make_catalogue(endpoint="http://other-lab.test/api/v1")
    write_catalogue(path, catalogue)
    with pytest.raises(ConfigError, match="endpoint"):
        read_catalogue(path, expected_endpoint="http://lims.test/api/v1")


def test_write_creates_parent_directory(tmp_path) -> None:
    path = tmp_path / "nested" / "dir" / "cat.json"
    catalogue = _make_catalogue()
    write_catalogue(path, catalogue)
    assert path.exists()


def test_write_atomic_replaces_previous_file(tmp_path) -> None:
    path = tmp_path / "cat.json"
    catalogue1 = _make_catalogue()
    write_catalogue(path, catalogue1)
    first_bytes = path.read_bytes()

    catalogue2 = OfflineCatalogue(
        schema_version=OFFLINE_CATALOGUE_VERSION,
        produced_by="LAB_STATION_02",
        produced_at="2026-05-06T00:00:00Z",
        lims_endpoint=catalogue1.lims_endpoint,
        projects=[],
    )
    write_catalogue(path, catalogue2)
    second_bytes = path.read_bytes()
    assert first_bytes != second_bytes
    parsed = read_catalogue(path, expected_endpoint=catalogue1.lims_endpoint)
    assert parsed.produced_by == "LAB_STATION_02"
    assert parsed.projects == []


def test_read_handles_missing_projects_array(tmp_path) -> None:
    path = tmp_path / "cat.json"
    payload = {
        "schema_version": OFFLINE_CATALOGUE_VERSION,
        "produced_by": "x",
        "produced_at": "now",
        "lims_endpoint": "http://lims.test/api/v1",
    }
    path.write_bytes(msgspec.json.encode(payload))
    parsed = read_catalogue(path, expected_endpoint="http://lims.test/api/v1")
    assert parsed.projects == []
