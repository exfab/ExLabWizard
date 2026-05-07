"""Tests for :class:`exlab_wizard.lims.cache.LIMSCache`.

The cache is the §7.2.4 SQLite TTL store; these tests pin upsert
semantics, status-filter behavior, and the ``is_fresh`` TTL window.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from exlab_wizard.lims.cache import LIMSCache
from exlab_wizard.lims.schemas import LIMSProject

pytestmark = pytest.mark.asyncio


def _project(
    *,
    short_id: str = "PROJ-0001",
    uid: str = "uid-1",
    status: str = "Active",
    fetched_at: str | None = None,
) -> LIMSProject:
    return LIMSProject(
        uid=uid,
        short_id=short_id,
        name=f"name-{short_id}",
        description=None,
        status=status,
        contact_name=None,
        owner="owner",
        metadata={"k": "v"},
        fetched_at=fetched_at or datetime.now(UTC).isoformat(),
    )


async def test_init_creates_table_idempotent(tmp_path) -> None:
    cache = LIMSCache(tmp_path / "cache.db")
    await cache.init()
    # A second init() must not fail (idempotent CREATE TABLE IF NOT EXISTS).
    await cache.init()
    await cache.close()


async def test_upsert_many_then_list(tmp_path) -> None:
    cache = LIMSCache(tmp_path / "cache.db")
    await cache.init()
    try:
        projects = [_project(short_id="PROJ-0001"), _project(short_id="PROJ-0002", uid="uid-2")]
        await cache.upsert_many("http://lims.test", projects)
        listed = await cache.list_projects("http://lims.test")
        assert sorted(p.short_id for p in listed) == ["PROJ-0001", "PROJ-0002"]
    finally:
        await cache.close()


async def test_upsert_many_empty_is_noop(tmp_path) -> None:
    cache = LIMSCache(tmp_path / "cache.db")
    await cache.init()
    try:
        await cache.upsert_many("http://lims.test", [])
        listed = await cache.list_projects("http://lims.test")
        assert listed == []
    finally:
        await cache.close()


async def test_upsert_updates_existing_row(tmp_path) -> None:
    cache = LIMSCache(tmp_path / "cache.db")
    await cache.init()
    try:
        await cache.upsert_many("http://lims.test", [_project(short_id="P")])
        # Re-upsert with new name and a fresh timestamp.
        new_time = (datetime.now(UTC) + timedelta(seconds=1)).isoformat()
        updated = LIMSProject(
            uid="uid-1",
            short_id="P",
            name="renamed",
            status="Completed",
            owner="owner",
            metadata={},
            fetched_at=new_time,
        )
        await cache.upsert_many("http://lims.test", [updated])
        listed = await cache.list_projects("http://lims.test")
        assert len(listed) == 1
        assert listed[0].name == "renamed"
        assert listed[0].status == "Completed"
    finally:
        await cache.close()


async def test_get_project_by_uid_or_short_id(tmp_path) -> None:
    cache = LIMSCache(tmp_path / "cache.db")
    await cache.init()
    try:
        await cache.upsert_many("http://lims.test", [_project(short_id="PROJ-0001", uid="uid-X")])
        by_short = await cache.get_project("http://lims.test", "PROJ-0001")
        by_uid = await cache.get_project("http://lims.test", "uid-X")
        assert by_short is not None and by_short.uid == "uid-X"
        assert by_uid is not None and by_uid.short_id == "PROJ-0001"
    finally:
        await cache.close()


async def test_get_project_missing_returns_none(tmp_path) -> None:
    cache = LIMSCache(tmp_path / "cache.db")
    await cache.init()
    try:
        result = await cache.get_project("http://lims.test", "nope")
        assert result is None
    finally:
        await cache.close()


async def test_list_projects_status_filter(tmp_path) -> None:
    cache = LIMSCache(tmp_path / "cache.db")
    await cache.init()
    try:
        await cache.upsert_many(
            "http://lims.test",
            [
                _project(short_id="A", uid="a", status="Active"),
                _project(short_id="B", uid="b", status="Archived"),
            ],
        )
        active = await cache.list_projects("http://lims.test", status_filter=["Active"])
        assert [p.short_id for p in active] == ["A"]
    finally:
        await cache.close()


async def test_is_fresh_true_when_within_ttl(tmp_path) -> None:
    cache = LIMSCache(tmp_path / "cache.db", ttl_hours=24)
    await cache.init()
    try:
        await cache.upsert_many("http://lims.test", [_project()])
        assert await cache.is_fresh("http://lims.test") is True
    finally:
        await cache.close()


async def test_is_fresh_false_when_stale(tmp_path) -> None:
    cache = LIMSCache(tmp_path / "cache.db", ttl_hours=1)
    await cache.init()
    try:
        old = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        await cache.upsert_many("http://lims.test", [_project(fetched_at=old)])
        assert await cache.is_fresh("http://lims.test") is False
    finally:
        await cache.close()


async def test_is_fresh_false_when_empty(tmp_path) -> None:
    cache = LIMSCache(tmp_path / "cache.db")
    await cache.init()
    try:
        assert await cache.is_fresh("http://lims.test") is False
    finally:
        await cache.close()


async def test_is_fresh_handles_zulu_suffix(tmp_path) -> None:
    cache = LIMSCache(tmp_path / "cache.db", ttl_hours=24)
    await cache.init()
    try:
        # Use a 'Z'-terminated timestamp to exercise the parser branch.
        await cache.upsert_many(
            "http://lims.test",
            [_project(fetched_at="2099-01-01T00:00:00Z")],
        )
        assert await cache.is_fresh("http://lims.test") is True
    finally:
        await cache.close()


async def test_is_fresh_ignores_invalid_timestamp(tmp_path) -> None:
    cache = LIMSCache(tmp_path / "cache.db", ttl_hours=24)
    await cache.init()
    try:
        await cache.upsert_many(
            "http://lims.test",
            [_project(fetched_at="not-a-date")],
        )
        assert await cache.is_fresh("http://lims.test") is False
    finally:
        await cache.close()


async def test_endpoint_isolates_rows(tmp_path) -> None:
    cache = LIMSCache(tmp_path / "cache.db")
    await cache.init()
    try:
        await cache.upsert_many("http://a", [_project(short_id="P", uid="ua")])
        await cache.upsert_many("http://b", [_project(short_id="P", uid="ub")])
        a = await cache.list_projects("http://a")
        b = await cache.list_projects("http://b")
        assert a[0].uid == "ua"
        assert b[0].uid == "ub"
    finally:
        await cache.close()


async def test_require_conn_before_init_raises(tmp_path) -> None:
    cache = LIMSCache(tmp_path / "cache.db")
    with pytest.raises(RuntimeError):
        await cache.list_projects("http://lims.test")


async def test_close_is_idempotent(tmp_path) -> None:
    cache = LIMSCache(tmp_path / "cache.db")
    await cache.init()
    await cache.close()
    # A second close after init must not raise.
    await cache.close()


async def test_is_fresh_handles_naive_timestamp(tmp_path) -> None:
    """A naive ISO timestamp (no offset) is treated as UTC by the cache."""
    cache = LIMSCache(tmp_path / "cache.db", ttl_hours=24)
    await cache.init()
    try:
        # Naive timestamp -- no tzinfo. The parser must default it to UTC.
        now = datetime.now(UTC).replace(tzinfo=None).isoformat()
        await cache.upsert_many("http://lims.test", [_project(fetched_at=now)])
        assert await cache.is_fresh("http://lims.test") is True
    finally:
        await cache.close()
