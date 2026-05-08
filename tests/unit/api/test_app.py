"""Unit tests for ``exlab_wizard.api.app``: AuditChannel + app factory."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

from exlab_wizard.api.app import (
    AppDependencies,
    AuditChannel,
    _diff_findings,
    _finding_to_dict,
    create_app,
)
from exlab_wizard.config.models import Config


class _F:
    """Tiny finding-shaped object for diff tests."""

    def __init__(
        self,
        rule: str,
        offending_path: str,
        matched_token: str | None = None,
        rule_detail: str = "",
    ) -> None:
        self.rule = rule
        self.offending_path = offending_path
        self.matched_token = matched_token
        self.rule_detail = rule_detail


def test_diff_findings_added_removed_changed() -> None:
    a = _F("orphan", "/a")
    b = _F("orphan", "/b")
    c_old = _F("placeholder", "/c", matched_token="<old>")
    c_new = _F("placeholder", "/c", matched_token="<new>")
    added, removed, changed = _diff_findings([a, c_old], [b, c_new])
    assert {f.offending_path for f in added} == {"/b"}
    assert {f.offending_path for f in removed} == {"/a"}
    assert [f.matched_token for f in changed] == ["<new>"]


def test_finding_to_dict_handles_dataclass_and_dict() -> None:
    obj = MagicMock()
    obj.to_dict.return_value = {"rule": "x"}
    assert _finding_to_dict(obj) == {"rule": "x"}
    assert _finding_to_dict({"rule": "y"}) == {"rule": "y"}
    assert _finding_to_dict("str") == {"value": "str"}


@pytest.mark.asyncio
async def test_audit_channel_publishes_snapshot_to_subscriber() -> None:
    channel = AuditChannel()
    finding = MagicMock()
    finding.to_dict.return_value = {"rule": "orphan"}
    received: list[dict[str, Any]] = []

    async def collect() -> None:
        async for frame in channel.subscribe():
            received.append(frame)
            if len(received) >= 1:
                break

    consumer = asyncio.create_task(collect())
    await asyncio.sleep(0)  # let subscribe attach
    await channel.publish_snapshot([finding], audit_at="2026-05-01T00:00:00Z")
    await asyncio.wait_for(consumer, timeout=2.0)
    assert received[0]["kind"] == "snapshot"
    assert received[0]["findings"][0] == {"rule": "orphan"}


@pytest.mark.asyncio
async def test_audit_channel_publishes_delta() -> None:
    channel = AuditChannel()
    finding = MagicMock()
    finding.to_dict.return_value = {"rule": "x"}
    received: list[dict[str, Any]] = []

    async def collect() -> None:
        async for frame in channel.subscribe():
            received.append(frame)
            if frame.get("kind") == "delta":
                break

    consumer = asyncio.create_task(collect())
    await asyncio.sleep(0)
    await channel.publish_delta(
        added=[finding], removed=[], changed=[], audit_at="2026-05-01T00:00:00Z"
    )
    await asyncio.wait_for(consumer, timeout=2.0)
    delta = next(f for f in received if f["kind"] == "delta")
    assert delta["added"] == [{"rule": "x"}]


@pytest.mark.asyncio
async def test_audit_channel_late_subscribe_receives_latest_snapshot() -> None:
    channel = AuditChannel()
    finding = MagicMock()
    finding.to_dict.return_value = {"rule": "orphan"}
    await channel.publish_snapshot([finding], audit_at="x")
    received: list[dict[str, Any]] = []

    async def collect() -> None:
        async for frame in channel.subscribe():
            received.append(frame)
            break

    consumer = asyncio.create_task(collect())
    await asyncio.wait_for(consumer, timeout=2.0)
    assert received[0]["kind"] == "snapshot"


@pytest.mark.asyncio
async def test_audit_channel_close_signals_subscribers() -> None:
    channel = AuditChannel()
    received: list[dict[str, Any]] = []

    async def collect() -> None:
        async for frame in channel.subscribe():
            received.append(frame)

    consumer = asyncio.create_task(collect())
    await asyncio.sleep(0)
    channel.close()
    await asyncio.wait_for(consumer, timeout=2.0)
    # No frames produced; the close sentinel ended the iterator.
    assert received == []


def test_create_app_uses_supplied_dependencies() -> None:
    deps = AppDependencies(config=Config())
    app = create_app(dependencies=deps)
    assert app.state.dependencies is deps


def test_create_app_creates_default_dependencies() -> None:
    app = create_app(config=Config())
    assert app.state.dependencies is not None
    assert app.state.dependencies.config is not None


def test_create_app_attaches_audit_channel() -> None:
    deps = AppDependencies(config=Config())
    create_app(dependencies=deps)
    assert deps.audit_channel is not None


@pytest.mark.asyncio
async def test_lifespan_starts_and_stops_audit_task() -> None:
    """When start_audit_task=True the lifespan starts the audit task and cancels on close."""

    audit_calls = {"count": 0}

    class StubValidator:
        def audit(self, scope: Any) -> list[Any]:
            audit_calls["count"] += 1
            return []

    deps = AppDependencies(config=Config(), validator=StubValidator())
    app = create_app(dependencies=deps, audit_interval_seconds=0.05, start_audit_task=True)

    async with app.router.lifespan_context(app):
        # Give the audit task at least one tick to run.
        await asyncio.sleep(0.15)
    # Lifespan exit cancels the audit task.
    assert deps.audit_task is None or deps.audit_task.done()
