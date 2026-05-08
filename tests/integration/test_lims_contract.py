"""Contract test against vendored upstream `mcnaughtonadm/exlab` snapshots.

Loads the JSON files in ``tests/fixtures/lims/exlab_v1/`` -- captures
of the four ``/api/v1`` endpoints ExLab-Wizard's read-only LIMS client
consumes -- and runs them through the same decoders the live client
uses (``LIMSClient._project_from_row`` and ``msgspec.convert(...,
LIMSUser)``). The test exists to catch wire-format drift the moment
the snapshots are re-grounded against a changed upstream; the weekly
``lims-live`` workflow is what re-grounds them.

No network is involved -- this test runs in the same fast PR pass as
the unit tests.
"""

from __future__ import annotations

import json
from pathlib import Path

import msgspec
import pytest

from exlab_wizard.lims.client import LIMSClient
from exlab_wizard.lims.schemas import LIMSProject, LIMSUser

_SNAPSHOTS = Path(__file__).resolve().parent.parent / "fixtures" / "lims" / "exlab_v1"


def _load(name: str) -> object:
    return json.loads((_SNAPSHOTS / name).read_text())


def test_me_snapshot_decodes_into_lims_user() -> None:
    """``GET /api/v1/me`` JSON must satisfy the ``LIMSUser`` schema."""
    user = msgspec.convert(_load("me.json"), LIMSUser)
    assert user.uid
    assert user.email
    assert user.role


def test_login_response_snapshot_decodes_into_lims_user() -> None:
    """Upstream's login body is the same ``safe_user`` shape as ``/me``."""
    user = msgspec.convert(_load("login_response.json"), LIMSUser)
    assert user.uid
    assert user.email
    assert user.role


def test_projects_list_snapshot_flows_through_list_projects_path() -> None:
    """``GET /api/v1/projects`` payload must yield ``LIMSProject`` rows.

    Mirrors the same envelope unwrap + per-row decode that
    ``LIMSClient.list_projects`` runs (`client.py`).
    """
    payload = _load("projects_list.json")
    assert isinstance(payload, dict), "list endpoint must return a dict envelope"
    rows = payload.get("data", [])
    assert rows, "snapshot must contain at least one project row"
    projects = [LIMSClient._project_from_row(row) for row in rows]
    for project in projects:
        assert isinstance(project, LIMSProject)
        assert project.uid
        assert project.short_id
        assert project.name


def test_project_one_snapshot_decodes_into_lims_project() -> None:
    """``GET /api/v1/projects/{id}`` returns one bare project object."""
    project = LIMSClient._project_from_row(_load("project_one.json"))
    assert isinstance(project, LIMSProject)
    assert project.uid
    assert project.short_id == "PROJ-0001"
    assert project.status == "Active"


@pytest.mark.parametrize(
    "snapshot",
    ["login_response.json", "me.json", "projects_list.json", "project_one.json"],
)
def test_snapshot_files_present_and_non_empty(snapshot: str) -> None:
    """Every required snapshot exists; guards against accidental deletion."""
    path = _SNAPSHOTS / snapshot
    assert path.is_file(), f"missing snapshot: {path}"
    assert path.stat().st_size > 0, f"empty snapshot: {path}"
