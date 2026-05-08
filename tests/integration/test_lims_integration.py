"""Integration tests for the LIMS client + cache pair. Backend Spec §7.2.

These tests wire :class:`LIMSClient` to the in-memory FastAPI fixture
(``tests/fixtures/mock_lims.py``) and to a fresh :class:`LIMSCache`,
then exercise the cache-first / refresh-on-stale flow end-to-end. The
flow under test is the §7.2.4 contract: list_projects populates the
cache; subsequent calls hit the cache when fresh; a TTL expiry forces a
refresh against the live API.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from exlab_wizard.lims.cache import LIMSCache
from exlab_wizard.lims.client import LIMSClient
from exlab_wizard.lims.schemas import LIMSProject
from tests.fixtures.mock_lims import make_mock_lims_app

pytestmark = pytest.mark.asyncio


def _make_client(app) -> LIMSClient:
    endpoint = os.environ.get("EXLAB_ENDPOINT")
    if endpoint:
        return LIMSClient(
            endpoint=endpoint,
            email=os.environ["EXLAB_EMAIL"],
            keyring_password_provider=lambda: os.environ["EXLAB_PASSWORD"],
        )
    client = LIMSClient(
        endpoint="http://lims.test",
        email="asmith@lab.example",
        keyring_password_provider=lambda: "secret",
    )
    transport = httpx.ASGITransport(app=app)
    client._client = httpx.AsyncClient(transport=transport, base_url="http://lims.test")
    return client


async def test_client_populates_cache(tmp_path) -> None:
    """list_projects() fills the cache; the cache then returns the same rows."""
    app = make_mock_lims_app()
    client = _make_client(app)
    cache = LIMSCache(tmp_path / "cache.db")
    await cache.init()
    try:
        await client.login()
        projects = await client.list_projects()
        await cache.upsert_many("http://lims.test", projects)
        assert await cache.is_fresh("http://lims.test") is True
        cached = await cache.list_projects("http://lims.test")
        assert {p.short_id for p in cached} == {p.short_id for p in projects}
    finally:
        await cache.close()
        await client.close()


async def test_cache_satisfies_lookup_without_network(tmp_path) -> None:
    """A get_project against the cache hits without a live LIMS call."""
    app = make_mock_lims_app()
    client = _make_client(app)
    cache = LIMSCache(tmp_path / "cache.db")
    await cache.init()
    try:
        await client.login()
        live = await client.list_projects()
        await cache.upsert_many("http://lims.test", live)
        # Now break the network by closing the client and verify the
        # cache still answers the lookup.
        await client.close()
        cached = await cache.get_project("http://lims.test", "PROJ-0001")
        assert cached is not None
        assert cached.short_id == "PROJ-0001"
    finally:
        await cache.close()


async def test_expired_cache_is_refreshed(tmp_path) -> None:
    """When cache TTL expires, a refresh from the live API rewrites rows."""
    app = make_mock_lims_app()
    client = _make_client(app)
    cache = LIMSCache(tmp_path / "cache.db", ttl_hours=1)
    await cache.init()
    try:
        # Seed the cache with a stale row directly.
        old_time = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        stale = LIMSProject(
            uid="old-uid",
            short_id="PROJ-OLD",
            name="stale",
            status="Active",
            owner="x",
            metadata={},
            fetched_at=old_time,
        )
        await cache.upsert_many("http://lims.test", [stale])
        assert await cache.is_fresh("http://lims.test") is False

        # Refresh from the network and re-stamp.
        await client.login()
        fresh_rows = await client.list_projects()
        await cache.upsert_many("http://lims.test", fresh_rows)
        assert await cache.is_fresh("http://lims.test") is True
        cached = await cache.list_projects("http://lims.test")
        assert any(p.short_id == "PROJ-0001" for p in cached)
    finally:
        await cache.close()
        await client.close()


async def test_relogin_then_cache_upsert(tmp_path) -> None:
    """A 401-then-relogin flow successfully refreshes and caches results."""
    app = make_mock_lims_app()
    client = _make_client(app)
    cache = LIMSCache(tmp_path / "cache.db")
    await cache.init()
    try:
        await client.login()
        app.state.mock_state.invalidate_sessions()  # force 401 on next read
        projects = await client.list_projects()
        await cache.upsert_many("http://lims.test", projects)
        cached = await cache.list_projects("http://lims.test")
        assert len(cached) == len(projects)
    finally:
        await cache.close()
        await client.close()


async def test_status_filter_consistent_between_client_and_cache(tmp_path) -> None:
    rows = [
        {
            "uid": "u1",
            "short_id": "PROJ-A",
            "name": "Active",
            "status": "Active",
            "owner": "x",
            "metadata": {},
        },
        {
            "uid": "u2",
            "short_id": "PROJ-B",
            "name": "Archived",
            "status": "Archived",
            "owner": "x",
            "metadata": {},
        },
    ]
    app = make_mock_lims_app(projects=rows)
    client = _make_client(app)
    cache = LIMSCache(tmp_path / "cache.db")
    await cache.init()
    try:
        await client.login()
        all_projects = await client.list_projects()
        await cache.upsert_many("http://lims.test", all_projects)
        live_active = await client.list_projects(status_filter=["Active"])
        cached_active = await cache.list_projects("http://lims.test", status_filter=["Active"])
        assert [p.short_id for p in live_active] == [p.short_id for p in cached_active]
    finally:
        await cache.close()
        await client.close()
