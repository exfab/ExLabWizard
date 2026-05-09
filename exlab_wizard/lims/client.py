"""Read-only LIMS client (Mapping B). Backend Spec §7.2.

The client wraps an :class:`httpx.AsyncClient` to talk to the LIMS REST
API. Authentication is cookie-session per §7.2.5: ``login()`` POSTs the
operator's email + password to ``/api/v1/login``, the underlying
``httpx`` client retains the session cookie, and subsequent reads
(``list_projects``, ``get_project``, ``get_me``) reuse it. On a 401
response from any of those reads, the client transparently re-runs
``login()`` once before failing -- the LIMS may have invalidated the
cookie out-of-band (server restart, session timeout) and a fresh login
recovers without surfacing a transient error to the caller.

The client is intentionally read-only. Per §7.2.8 the v1 LIMS write
surface is the empty set; the only mutating call is ``login`` and that
is auth bookkeeping, not project mutation.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
import msgspec

from exlab_wizard.errors import ConfigError
from exlab_wizard.lims.schemas import HealthStatus, LIMSProject, LIMSUser
from exlab_wizard.logging import get_logger

__all__ = ["LIMSClient"]

logger = get_logger(__name__)

_LOGIN_PATH: str = "/api/v1/login"
_ME_PATH: str = "/api/v1/me"
_PROJECTS_PATH: str = "/api/v1/projects"
_DEFAULT_TIMEOUT_SECONDS: float = 15.0


class LIMSClient:
    """Read-only LIMS client. Backend Spec §7.2 Mapping B.

    Cookie-session auth: :meth:`login` establishes the session; subsequent
    list/get methods reuse the cookie. On 401, the client refreshes the
    cookie via :meth:`login` once before failing.

    The ``keyring_password_provider`` callable is invoked from
    :meth:`login` when no explicit password is passed. Callers wire this
    to :class:`exlab_wizard.lims.keyring_store.KeyringStore.get_password`
    in production; tests can pass a lambda that returns a static value.
    """

    def __init__(
        self,
        *,
        endpoint: str,
        email: str,
        keyring_password_provider: Callable[[], str | None],
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._email = email
        self._password_provider = keyring_password_provider
        self._client = httpx.AsyncClient(
            base_url=self._endpoint,
            timeout=_DEFAULT_TIMEOUT_SECONDS,
        )

    @property
    def endpoint(self) -> str:
        """Configured base URL (trailing slashes stripped)."""
        return self._endpoint

    @property
    def email(self) -> str:
        """Configured operator email; used as the login username."""
        return self._email

    async def login(self, *, password: str | None = None) -> None:
        """POST ``/api/v1/login`` with email + password.

        On success the underlying ``httpx.AsyncClient`` retains the
        session cookie automatically; subsequent reads reuse it.

        Raises :class:`exlab_wizard.errors.ConfigError` when the keyring
        provider returns no password and none was supplied -- that is a
        configuration condition, not a transient network failure.
        """
        secret = password if password is not None else self._password_provider()
        if not secret:
            msg = "LIMS password is not set in the keyring"
            raise ConfigError(msg)
        response = await self._client.post(
            _LOGIN_PATH,
            json={"email": self._email, "password": secret},
        )
        if response.status_code != 200:
            logger.warning(
                "lims.login_failed",
                extra={"status": response.status_code, "endpoint": self._endpoint},
            )
            response.raise_for_status()

    async def list_projects(self, *, status_filter: list[str] | None = None) -> list[LIMSProject]:
        """``GET /api/v1/projects``; returns one LIMSProject per row.

        ``status_filter`` is an optional list of allowed status values;
        rows whose ``status`` is not in the set are dropped on the
        client side. Filtering happens after deserialization so the
        wire format stays uniform.

        Wire envelope: upstream returns ``{"data": [...], "count": N}``;
        a missing ``data`` key is treated as an empty list rather than
        propagating a ``KeyError`` to the caller.
        """
        payload = await self._get_json(_PROJECTS_PATH)
        rows = payload.get("data", [])
        projects = [self._project_from_row(row) for row in rows]
        if status_filter:
            allowed = set(status_filter)
            projects = [p for p in projects if p.status in allowed]
        return projects

    async def get_project(self, uid_or_short_id: str) -> LIMSProject | None:
        """``GET /api/v1/projects/<id>``; returns None on 404.

        ``uid_or_short_id`` may be either a UUID (``uid`` column) or a
        ``PROJ-NNNN`` string (``short_id`` column). The LIMS resolves
        both at the same endpoint.
        """
        url = f"{_PROJECTS_PATH}/{uid_or_short_id}"
        response = await self._request_with_relogin("GET", url)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return self._project_from_row(response.json())

    async def get_me(self) -> LIMSUser:
        """``GET /api/v1/me``; returns the current operator's row."""
        payload = await self._get_json(_ME_PATH)
        return msgspec.convert(payload, LIMSUser)

    async def health_check(self) -> HealthStatus:
        """Return a ``HealthStatus`` snapshot. Backend Spec §7.2.3.

        Calls ``GET /api/v1/me`` and times the response. On any error
        (network, 4xx, 5xx) returns ``ok=False`` with a short reason
        rather than raising -- the Settings "Test connection" UX needs
        a value to render.
        """
        start = time.monotonic()
        try:
            response = await self._request_with_relogin("GET", _ME_PATH)
        except (httpx.HTTPError, ConfigError) as exc:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            return HealthStatus(ok=False, latency_ms=elapsed_ms, reason=str(exc))
        elapsed_ms = int((time.monotonic() - start) * 1000)
        if response.status_code // 100 == 2:
            return HealthStatus(ok=True, latency_ms=elapsed_ms, reason=None)
        return HealthStatus(
            ok=False,
            latency_ms=elapsed_ms,
            reason=f"HTTP {response.status_code}",
        )

    async def close(self) -> None:
        """Close the underlying ``httpx.AsyncClient``. Idempotent."""
        await self._client.aclose()

    # ------------------------------------------------------------------
    # internal
    # ------------------------------------------------------------------

    async def _get_json(self, path: str) -> Any:
        response = await self._request_with_relogin("GET", path)
        response.raise_for_status()
        return response.json()

    async def _request_with_relogin(self, method: str, path: str) -> httpx.Response:
        """Issue one request; on 401 retry once after a fresh ``login``."""
        response = await self._client.request(method, path)
        if response.status_code != 401:
            return response
        logger.info("lims.relogin_after_401", extra={"path": path})
        await self.login()
        return await self._client.request(method, path)

    @staticmethod
    def _project_from_row(row: dict[str, Any]) -> LIMSProject:
        """Decode one ``/projects`` row into LIMSProject.

        Adds a fresh UTC ``fetched_at`` so cache writers can stamp the
        row deterministically from a single network refresh.
        """
        return LIMSProject(
            uid=row["uid"],
            short_id=row["short_id"],
            name=row["name"],
            description=row.get("description"),
            status=row["status"],
            contact_name=row.get("contact_name"),
            owner=row["owner"],
            metadata=row.get("metadata", {}),
            fetched_at=_utc_now_iso(),
        )


def _utc_now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string with seconds precision."""
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
