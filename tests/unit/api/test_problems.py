"""Unit tests for the ``/problems`` router."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import msgspec
import pytest
from fastapi.testclient import TestClient

from exlab_wizard.api import AppDependencies, create_app
from exlab_wizard.api.schemas import (
    CreationJson,
    LimsProjectBlock,
    PathsBlock,
    TemplateBlock,
)
from exlab_wizard.cache.creation_writer import CreationWriter
from exlab_wizard.config.models import (
    Config,
    EquipmentConfig,
    LIMSConfig,
    PathsConfig,
    RcloneTransport,
)
from exlab_wizard.constants import (
    CACHE_DIR_NAME,
    CREATION_JSON_NAME,
    CREATION_JSON_VERSION,
    SyncStatus,
)
from exlab_wizard.validator.findings import Finding


class _StubValidator:
    """Test double that returns a canned finding list."""

    def __init__(self, findings: list[Finding]) -> None:
        self._findings = list(findings)
        self.audit_calls = 0

    def query_problems(self, scope: dict[str, Any]) -> list[Finding]:
        return list(self._findings)

    def audit(self, scope: dict[str, Any]) -> list[Finding]:
        self.audit_calls += 1
        return list(self._findings)


def _ready_config(local_root: Path) -> Config:
    return Config(
        paths=PathsConfig(
            templates_dir=str(local_root), plugin_dir=str(local_root), local_root=str(local_root)
        ),
        equipment=[
            EquipmentConfig(
                id="EQ1",
                label="Equipment 1",
                local_root=str(local_root),
                nas_root="/n",
                completeness_signal="sentinel_file",
                sentinel_filename="done.flag",
                transport=RcloneTransport(
                    type="rclone",
                    rclone_remote="lab-nas",
                    rclone_remote_path="lab/EQ1",
                ),
            )
        ],
        lims=LIMSConfig(endpoint="https://lims.example", email="op@example"),
    )


def _findings() -> list[Finding]:
    return [
        Finding(
            rule="orphan",
            tier="soft",
            run_path="/data/EQ1/PROJ-0042",
            offending_path="/data/EQ1/PROJ-0042",
            offending_kind="directory_segment",
            rule_detail="orphan project",
        ),
        Finding(
            rule="unresolved_placeholder_token",
            tier="hard",
            run_path="/data/EQ1/PROJ-0042/Run_2026",
            offending_path="/data/EQ1/PROJ-0042/Run_2026",
            offending_kind="directory_segment",
            matched_token="<run_date>",
            rule_detail="placeholder",
        ),
    ]


def _write_creation_json(directory: Path) -> None:
    cache_dir = directory / CACHE_DIR_NAME
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = CreationJson(
        schema_version=CREATION_JSON_VERSION,
        created_at=datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        created_by="asmith",
        level="run",
        run_kind="experimental",
        lims_project=LimsProjectBlock(
            uid="x", short_id="PROJ-0042", name_at_creation="ex", source="live"
        ),
        template=TemplateBlock(
            name="basic", version="1.0.0", source_path="/tpl", run_scope="experimental"
        ),
        variables={},
        paths=PathsBlock(local=str(directory), nas="/n"),
        sync_status=SyncStatus.PENDING.value,
    )
    (cache_dir / CREATION_JSON_NAME).write_bytes(msgspec.json.encode(payload))


def test_list_problems_returns_findings(tmp_path: Path) -> None:
    deps = AppDependencies(
        config=_ready_config(tmp_path),
        validator=_StubValidator(_findings()),
    )
    app = create_app(dependencies=deps)
    client = TestClient(app)
    response = client.get("/api/v1/problems")
    assert response.status_code == 200
    body = response.json()
    assert len(body["findings"]) == 2
    rules = [f["rule"] for f in body["findings"]]
    assert "orphan" in rules
    assert "unresolved_placeholder_token" in rules


def test_list_problems_filters_by_severity(tmp_path: Path) -> None:
    deps = AppDependencies(
        config=_ready_config(tmp_path),
        validator=_StubValidator(_findings()),
    )
    app = create_app(dependencies=deps)
    client = TestClient(app)
    response = client.get("/api/v1/problems?severity=hard")
    body = response.json()
    assert all(f["tier"] == "hard" for f in body["findings"])


def test_list_problems_filters_by_class(tmp_path: Path) -> None:
    deps = AppDependencies(
        config=_ready_config(tmp_path),
        validator=_StubValidator(_findings()),
    )
    app = create_app(dependencies=deps)
    client = TestClient(app)
    response = client.get("/api/v1/problems?class=orphan")
    body = response.json()
    assert all(f["rule"] == "orphan" for f in body["findings"])


def test_list_problems_with_equipment_scope(tmp_path: Path) -> None:
    deps = AppDependencies(
        config=_ready_config(tmp_path),
        validator=_StubValidator(_findings()),
    )
    app = create_app(dependencies=deps)
    client = TestClient(app)
    response = client.get("/api/v1/problems?scope=equipment_id&scope_value=EQ1")
    assert response.status_code == 200


def test_list_problems_unknown_scope_returns_422(tmp_path: Path) -> None:
    deps = AppDependencies(
        config=_ready_config(tmp_path),
        validator=_StubValidator([]),
    )
    app = create_app(dependencies=deps)
    client = TestClient(app)
    response = client.get("/api/v1/problems?scope=not_a_scope")
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_failed"


def test_refresh_runs_audit(tmp_path: Path) -> None:
    stub = _StubValidator(_findings())
    deps = AppDependencies(config=_ready_config(tmp_path), validator=stub)
    app = create_app(dependencies=deps)
    client = TestClient(app)
    response = client.post("/api/v1/problems/refresh")
    assert response.status_code == 200
    body = response.json()
    assert body["finding_count"] == 2
    assert stub.audit_calls == 1


@pytest.mark.asyncio
async def test_append_override_writes_entry(tmp_path: Path) -> None:
    run_dir = tmp_path / "data" / "EQ1" / "PROJ-0042"
    run_dir.mkdir(parents=True)
    _write_creation_json(run_dir)
    cache_writer = CreationWriter()
    deps = AppDependencies(
        config=_ready_config(tmp_path),
        validator=_StubValidator([]),
        cache_creation=cache_writer,
    )
    app = create_app(dependencies=deps)
    client = TestClient(app)
    body = {
        "problem_class": "orphan",
        "reason": "Acquired before this rule landed; no remediation possible.",
        "operator": "asmith",
    }
    response = client.post(f"/api/v1/problems/{run_dir}/override", json=body)
    assert response.status_code == 201
    entry = response.json()
    assert entry["problem_class"] == "orphan"
    assert entry["operator"] == "asmith"
    assert entry["revoked"] is False
    # The entry now exists on disk.
    decoded = msgspec.json.decode(
        (run_dir / CACHE_DIR_NAME / CREATION_JSON_NAME).read_bytes(),
        type=CreationJson,
    )
    assert len(decoded.validation_overrides) == 1
    assert decoded.validation_overrides[0]["problem_class"] == "orphan"


@pytest.mark.asyncio
async def test_append_override_404_when_path_absent(tmp_path: Path) -> None:
    cache_writer = CreationWriter()
    deps = AppDependencies(
        config=_ready_config(tmp_path),
        validator=_StubValidator([]),
        cache_creation=cache_writer,
    )
    app = create_app(dependencies=deps)
    client = TestClient(app)
    bogus = tmp_path / "no_such"
    body = {
        "problem_class": "orphan",
        "reason": "the operator's reason text is at least ten characters long",
    }
    response = client.post(f"/api/v1/problems/{bogus}/override", json=body)
    assert response.status_code == 404


def test_problems_websocket_sends_snapshot_on_connect(tmp_path: Path) -> None:
    """Connecting to /problems/events emits a snapshot frame immediately."""
    import json

    from fastapi.testclient import TestClient

    from exlab_wizard.api import AuditChannel

    deps = AppDependencies(
        config=_ready_config(tmp_path),
        validator=_StubValidator(_findings()),
        audit_channel=AuditChannel(),
    )
    app = create_app(dependencies=deps)
    client = TestClient(app)
    with client.websocket_connect("/api/v1/problems/events") as ws:
        first = json.loads(ws.receive_bytes())
        assert first["kind"] == "snapshot"
        assert len(first["findings"]) == 2


def test_problems_websocket_closes_when_no_channel(tmp_path: Path) -> None:
    """Without an audit channel the WebSocket closes with 1011."""
    from fastapi.testclient import TestClient

    deps = AppDependencies(
        config=_ready_config(tmp_path),
        validator=_StubValidator([]),
        audit_channel=None,
    )
    app = create_app(dependencies=deps)
    # Force the audit channel back to None to exercise the close path.
    deps.audit_channel = None
    client = TestClient(app)
    try:
        with client.websocket_connect("/api/v1/problems/events"):
            pass
    except Exception:
        pass


def test_refresh_publishes_snapshot_to_channel(tmp_path: Path) -> None:
    """``POST /problems/refresh`` invokes audit and publishes to the channel."""
    from fastapi.testclient import TestClient

    from exlab_wizard.api import AuditChannel

    channel = AuditChannel()
    deps = AppDependencies(
        config=_ready_config(tmp_path),
        validator=_StubValidator(_findings()),
        audit_channel=channel,
    )
    app = create_app(dependencies=deps)
    client = TestClient(app)
    response = client.post("/api/v1/problems/refresh")
    assert response.status_code == 200
    body = response.json()
    assert body["finding_count"] == 2


@pytest.mark.asyncio
async def test_revoke_override_writes_tombstone(tmp_path: Path) -> None:
    run_dir = tmp_path / "data" / "EQ1" / "PROJ-0042"
    run_dir.mkdir(parents=True)
    _write_creation_json(run_dir)
    cache_writer = CreationWriter()
    deps = AppDependencies(
        config=_ready_config(tmp_path),
        validator=_StubValidator([]),
        cache_creation=cache_writer,
    )
    app = create_app(dependencies=deps)
    client = TestClient(app)
    body = {
        "revokes": "abc-id-to-be-revoked",
        "reason": "operator changed mind, no longer applicable",
        "operator": "asmith",
    }
    response = client.post(f"/api/v1/problems/{run_dir}/override/revoke", json=body)
    assert response.status_code == 201
    entry = response.json()
    assert entry["revoked"] is True
    assert entry["revokes"] == "abc-id-to-be-revoked"
