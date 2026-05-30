"""Unit tests for the GitHub releases fetch. Design Spec §15.6 / §15.8 item 3."""

from __future__ import annotations

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from exlab_wizard.update_check.checker import fetch_latest_tag

# The checker GETs the absolute ``API_LATEST_URL``; its path component is the
# repo's releases/latest endpoint. The stub app mounts exactly that path so an
# ASGITransport-backed client (base_url = api.github.com) routes the request.
_LATEST_PATH = "/repos/exfab/ExLabWizard/releases/latest"
_HTML_URL = "https://github.com/exfab/ExLabWizard/releases/v9.9.9"


def _client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(base_url="https://api.github.com", transport=transport)


async def test_fetch_returns_tag_and_url() -> None:
    app = FastAPI()

    @app.get(_LATEST_PATH)
    async def latest() -> dict[str, str]:
        return {"tag_name": "v9.9.9", "html_url": _HTML_URL}

    async with _client(app) as client:
        result = await fetch_latest_tag(client)

    assert result == ("v9.9.9", _HTML_URL)


async def test_fetch_returns_none_on_403() -> None:
    app = FastAPI()

    @app.get(_LATEST_PATH)
    async def latest() -> JSONResponse:
        # GitHub returns 403 when rate-limited / unidentified.
        return JSONResponse(status_code=403, content={"message": "rate limited"})

    async with _client(app) as client:
        result = await fetch_latest_tag(client)

    assert result is None


async def test_fetch_returns_none_on_500() -> None:
    app = FastAPI()

    @app.get(_LATEST_PATH)
    async def latest() -> JSONResponse:
        return JSONResponse(status_code=500, content={"message": "boom"})

    async with _client(app) as client:
        result = await fetch_latest_tag(client)

    assert result is None
