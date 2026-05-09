"""Tests for :class:`exlab_wizard.lims.client.LIMSClient`.

Each test wires the client to the in-memory FastAPI fixture from
``tests/fixtures/mock_lims.py`` via :class:`httpx.ASGITransport`, so
the wire format and cookie-handling paths are exercised end-to-end
without a real network port.
"""

from __future__ import annotations

import httpx
import pytest

from exlab_wizard.errors import ConfigError
from exlab_wizard.lims.client import LIMSClient
from exlab_wizard.lims.schemas import HealthStatus, LIMSProject, LIMSUser
from tests.fixtures.mock_lims import make_mock_lims_app

pytestmark = pytest.mark.asyncio


def _make_client(app, *, password: str | None = "secret") -> LIMSClient:
    client = LIMSClient(
        endpoint="http://lims.test",
        email="asmith@lab.example",
        keyring_password_provider=lambda: password,
    )
    transport = httpx.ASGITransport(app=app)
    # Replace the network transport with the in-process FastAPI app while
    # keeping the same cookie jar / base URL the client constructed.
    client._client = httpx.AsyncClient(transport=transport, base_url="http://lims.test")
    return client


async def test_login_happy_path() -> None:
    app = make_mock_lims_app()
    client = _make_client(app)
    try:
        await client.login()
        # The cookie was set; subsequent `/me` succeeds.
        user = await client.get_me()
        assert isinstance(user, LIMSUser)
        assert user.email == "asmith@lab.example"
    finally:
        await client.close()


async def test_login_uses_explicit_password_over_provider() -> None:
    app = make_mock_lims_app()
    client = _make_client(app, password="WRONG")
    try:
        await client.login(password="secret")
        user = await client.get_me()
        assert user.email == "asmith@lab.example"
    finally:
        await client.close()


async def test_login_wrong_password_raises() -> None:
    app = make_mock_lims_app()
    client = _make_client(app, password="WRONG")
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await client.login()
    finally:
        await client.close()


async def test_login_with_no_password_raises_config_error() -> None:
    app = make_mock_lims_app()
    client = _make_client(app, password=None)
    try:
        with pytest.raises(ConfigError):
            await client.login()
    finally:
        await client.close()


async def test_list_projects_returns_typed_rows() -> None:
    app = make_mock_lims_app()
    client = _make_client(app)
    try:
        await client.login()
        projects = await client.list_projects()
        assert len(projects) == 1
        assert isinstance(projects[0], LIMSProject)
        assert projects[0].short_id == "PROJ-0001"
        assert projects[0].fetched_at  # client stamps a timestamp
    finally:
        await client.close()


async def test_list_projects_status_filter() -> None:
    rows = [
        {
            "uid": "u1",
            "short_id": "PROJ-0001",
            "name": "Active project",
            "status": "Active",
            "owner": "x",
            "metadata": {},
        },
        {
            "uid": "u2",
            "short_id": "PROJ-0002",
            "name": "Archived project",
            "status": "Archived",
            "owner": "x",
            "metadata": {},
        },
    ]
    app = make_mock_lims_app(projects=rows)
    client = _make_client(app)
    try:
        await client.login()
        active = await client.list_projects(status_filter=["Active"])
        assert [p.short_id for p in active] == ["PROJ-0001"]
    finally:
        await client.close()


async def test_get_project_by_uid() -> None:
    app = make_mock_lims_app()
    client = _make_client(app)
    try:
        await client.login()
        project = await client.get_project("proj-uid-1")
        assert project is not None
        assert project.uid == "proj-uid-1"
    finally:
        await client.close()


async def test_get_project_by_short_id() -> None:
    app = make_mock_lims_app()
    client = _make_client(app)
    try:
        await client.login()
        project = await client.get_project("PROJ-0001")
        assert project is not None
        assert project.short_id == "PROJ-0001"
    finally:
        await client.close()


async def test_get_project_404_returns_none() -> None:
    app = make_mock_lims_app()
    client = _make_client(app)
    try:
        await client.login()
        result = await client.get_project("does-not-exist")
        assert result is None
    finally:
        await client.close()


async def test_get_me() -> None:
    app = make_mock_lims_app()
    client = _make_client(app)
    try:
        await client.login()
        user = await client.get_me()
        assert user.uid == "user-uid-1"
        assert user.role == "Admin"
    finally:
        await client.close()


async def test_health_check_ok() -> None:
    app = make_mock_lims_app()
    client = _make_client(app)
    try:
        await client.login()
        status = await client.health_check()
        assert isinstance(status, HealthStatus)
        assert status.ok is True
        assert status.latency_ms >= 0
    finally:
        await client.close()


async def test_health_check_failure_returns_status() -> None:
    """``health_check`` MUST NOT raise. Per Backend Spec §7.2.3."""
    app = make_mock_lims_app()
    client = _make_client(app, password=None)
    try:
        # Never logged in; first /me returns 401, the relogin attempt
        # raises ConfigError because no password was provided. The
        # client must trap that and return a HealthStatus instead.
        status = await client.health_check()
        assert status.ok is False
        assert status.reason is not None
    finally:
        await client.close()


async def test_relogin_after_401() -> None:
    """When the LIMS invalidates the cookie mid-flight, the client
    transparently re-runs ``login`` once and retries the request.
    """
    app = make_mock_lims_app()
    client = _make_client(app)
    try:
        await client.login()
        # Drop the issued session token so the next /projects returns 401.
        app.state.mock_state.invalidate_sessions()
        projects = await client.list_projects()
        assert len(projects) == 1
    finally:
        await client.close()


async def test_health_check_returns_non200_status() -> None:
    """When the LIMS returns a non-2xx status, ``health_check`` returns
    ``ok=False`` with an HTTP-coded reason, never raising.
    """
    from fastapi import FastAPI

    # A second app that returns 500 from /me without ever issuing 401,
    # so the request lands in the non-2xx branch of health_check.
    server_500 = FastAPI()

    @server_500.get("/api/v1/me")
    async def _me_500() -> dict[str, str]:
        from fastapi import HTTPException

        raise HTTPException(status_code=500, detail="boom")

    @server_500.post("/api/v1/login")
    async def _login_ok(payload: dict) -> dict[str, str]:
        return {"status": "ok"}

    client = _make_client(server_500)
    try:
        await client.login()
        status = await client.health_check()
        assert status.ok is False
        assert status.reason == "HTTP 500"
    finally:
        await client.close()


async def test_endpoint_strips_trailing_slash() -> None:
    client = LIMSClient(
        endpoint="http://lims.test/",
        email="x@y",
        keyring_password_provider=lambda: "secret",
    )
    try:
        assert client.endpoint == "http://lims.test"
        assert client.email == "x@y"
    finally:
        await client.close()
