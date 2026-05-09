"""In-memory FastAPI fixture LIMS server used by the LIMS-client tests.

The fixture mirrors the real upstream contract from
``gitlab.com/mcnaughtonadm/exlab`` (Backend Spec §7.2):

- ``POST /api/v1/login`` -- email/password login. Sets a session
  cookie on success, returns 401 otherwise.
- ``GET /api/v1/me`` -- current user, returning the upstream
  ``safe_user`` shape (``{id, uid, email, role, created_at,
  updated_at}``). Cookie required.
- ``GET /api/v1/projects`` -- project list wrapped in the upstream
  envelope ``{"data": [...], "count": N}``. Cookie required.
- ``GET /api/v1/projects/{uid_or_short_id}`` -- one project. Cookie
  required.

Cookie validation is intentionally simple: a valid cookie value is any
string the server itself issued. Tests that exercise the 401-then-relogin
path can call :func:`MockLimsState.invalidate_sessions` to drop the
issued tokens and force a re-login on the next read.

The app is constructed via :func:`make_mock_lims_app` and mounted on
:class:`httpx.AsyncClient` directly through ``transport=ASGITransport``
so tests stay in-process and do not bind a port.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from typing import Any

from fastapi import Cookie, FastAPI, HTTPException, Response

_COOKIE_NAME: str = "session"


@dataclass
class MockLimsState:
    """Mutable per-app state.

    ``valid_email`` / ``valid_password`` are the credentials the
    fixture's ``POST /login`` accepts. ``projects`` is a list of dict
    rows -- the wire format the client decodes. ``users`` maps email
    to user dict.
    """

    valid_email: str
    valid_password: str
    projects: list[dict[str, Any]] = field(default_factory=list)
    users: dict[str, dict[str, Any]] = field(default_factory=dict)
    issued_sessions: set[str] = field(default_factory=set)

    def issue_session(self) -> str:
        """Mint a fresh opaque session token."""
        token = secrets.token_hex(8)
        self.issued_sessions.add(token)
        return token

    def is_valid_session(self, token: str | None) -> bool:
        return token is not None and token in self.issued_sessions

    def invalidate_sessions(self) -> None:
        """Drop every issued session token. The next protected call
        sees a 401 and triggers the client's ``/login`` retry path.
        """
        self.issued_sessions.clear()


def _default_user(email: str) -> dict[str, Any]:
    return {
        "uid": "user-uid-1",
        "email": email,
        "role": "Admin",
    }


def _default_project() -> dict[str, Any]:
    return {
        "uid": "proj-uid-1",
        "short_id": "PROJ-0001",
        "name": "Cortex Q3 Pilot",
        "description": "Description.",
        "status": "Active",
        "contact_name": "Lab Lead",
        "owner": "asmith@lab.example",
        "metadata": {"key": "value"},
    }


def make_mock_lims_app(
    *,
    valid_email: str = "asmith@lab.example",
    valid_password: str = "secret",
    projects: list[dict[str, Any]] | None = None,
    users: dict[str, dict[str, Any]] | None = None,
) -> FastAPI:
    """Build a FastAPI app implementing the v1 LIMS read surface.

    Returns a fresh :class:`FastAPI` application. The associated
    :class:`MockLimsState` is attached as ``app.state.mock_state`` so
    test code can mutate the project list or revoke sessions
    mid-request to exercise the 401 retry path.
    """
    app = FastAPI()
    state = MockLimsState(
        valid_email=valid_email,
        valid_password=valid_password,
        projects=list(projects) if projects is not None else [_default_project()],
        users=dict(users) if users is not None else {valid_email: _default_user(valid_email)},
    )
    app.state.mock_state = state

    def _require_session(session: str | None) -> None:
        if not state.is_valid_session(session):
            raise HTTPException(status_code=401, detail="not authenticated")

    @app.post("/api/v1/login")
    async def login(payload: dict[str, str], response: Response) -> dict[str, str]:
        if (
            payload.get("email") != state.valid_email
            or payload.get("password") != state.valid_password
        ):
            raise HTTPException(status_code=401, detail="bad credentials")
        token = state.issue_session()
        response.set_cookie(_COOKIE_NAME, token, httponly=True)
        return {"status": "ok"}

    @app.get("/api/v1/me")
    async def me(session: str | None = Cookie(default=None)) -> dict[str, Any]:
        _require_session(session)
        return state.users[state.valid_email]

    @app.get("/api/v1/projects")
    async def list_projects(
        session: str | None = Cookie(default=None),
    ) -> dict[str, Any]:
        _require_session(session)
        return {"data": state.projects, "count": len(state.projects)}

    @app.get("/api/v1/projects/{ident}")
    async def get_project(
        ident: str,
        session: str | None = Cookie(default=None),
    ) -> dict[str, Any]:
        _require_session(session)
        for project in state.projects:
            if ident in (project.get("uid"), project.get("short_id")):
                return project
        raise HTTPException(status_code=404, detail="not found")

    return app
