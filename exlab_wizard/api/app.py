"""FastAPI app + lifespan + dependency wiring. Backend Spec §4.6.

The :func:`create_app` factory builds the app with the §4.6 versioned
prefix (``/api/v1/...``), mounts every router, registers exception
handlers (§4.6.3 envelope), and binds an :class:`AppDependencies`
instance onto ``app.state.dependencies`` so per-request handlers can
look up the live controller / validator / cache writers / etc.

Lifespan responsibilities (§4.5):

* Load (or accept the supplied) ``config.yaml``.
* Build the plugin registry (best-effort; failure logs WARN).
* Refresh the LIMS project cache (best-effort).
* Start the background audit task (every 30 s; pub-sub publishes
  deltas on the ``/problems/events`` channel).
* On shutdown, drain in-flight sessions, stop the audit task, and
  close the cache writers.

The launcher (in production) constructs a full :class:`AppDependencies`
with real components; tests can pass a custom dependencies object whose
fields are stubs / mocks.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, FastAPI

from exlab_wizard import __version__
from exlab_wizard.api.errors import register_exception_handlers
from exlab_wizard.api.health import build_health_router
from exlab_wizard.api.routers.browse import build_browse_router
from exlab_wizard.api.routers.config import build_config_router
from exlab_wizard.api.routers.operations import build_operations_router
from exlab_wizard.api.routers.problems import build_problems_router
from exlab_wizard.api.routers.sessions import build_sessions_router
from exlab_wizard.api.routers.staging import build_staging_router
from exlab_wizard.api.setup import build_setup_router
from exlab_wizard.config.models import Config
from exlab_wizard.constants import AUDIT_REFRESH_SECONDS
from exlab_wizard.logging import get_logger

__all__ = ["AppDependencies", "AuditChannel", "create_app"]

_log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Audit pub-sub channel
# ---------------------------------------------------------------------------


class AuditChannel:
    """Multi-subscriber pub-sub for the Problems WebSocket. Backend Spec §4.6.2.

    Subscribers receive every published frame (snapshot or delta). The
    channel keeps the most recent snapshot so late subscribers do not
    have to wait for the next 30-second tick.
    """

    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue[dict[str, Any]]] = []
        self._latest_snapshot: dict[str, Any] | None = None

    def subscribe(self) -> AsyncIterator[dict[str, Any]]:
        """Return an async iterator that yields every published frame."""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._subscribers.append(queue)
        if self._latest_snapshot is not None:
            queue.put_nowait(self._latest_snapshot)
        return _drain(queue, self._subscribers)

    async def publish_snapshot(self, findings: list[Any], audit_at: str) -> None:
        frame = {
            "kind": "snapshot",
            "findings": [_finding_to_dict(f) for f in findings],
            "audit_at": audit_at,
        }
        self._latest_snapshot = frame
        await self._broadcast(frame)

    async def publish_delta(
        self,
        *,
        added: list[Any],
        removed: list[Any],
        changed: list[Any],
        audit_at: str,
    ) -> None:
        frame = {
            "kind": "delta",
            "added": [_finding_to_dict(f) for f in added],
            "removed": [_finding_to_dict(f) for f in removed],
            "changed": [_finding_to_dict(f) for f in changed],
            "audit_at": audit_at,
        }
        await self._broadcast(frame)

    async def _broadcast(self, frame: dict[str, Any]) -> None:
        for q in list(self._subscribers):
            with contextlib.suppress(Exception):
                q.put_nowait(frame)

    def close(self) -> None:
        """Close every subscriber's queue. Idempotent."""
        for q in self._subscribers:
            with contextlib.suppress(Exception):
                q.put_nowait({"kind": "__closed__"})
        self._subscribers.clear()


async def _drain(
    queue: asyncio.Queue[dict[str, Any]],
    registry: list[asyncio.Queue[dict[str, Any]]],
) -> AsyncIterator[dict[str, Any]]:
    """Yield from the subscriber queue until a sentinel arrives."""
    try:
        while True:
            frame = await queue.get()
            if frame.get("kind") == "__closed__":
                return
            yield frame
    finally:
        with contextlib.suppress(ValueError):
            registry.remove(queue)


def _finding_to_dict(finding: Any) -> dict[str, Any]:
    """Best-effort serialize a Finding-like object to dict."""
    if hasattr(finding, "to_dict"):
        return finding.to_dict()
    if isinstance(finding, dict):
        return finding
    return {"value": str(finding)}


# ---------------------------------------------------------------------------
# AppDependencies
# ---------------------------------------------------------------------------


@dataclass
class AppDependencies:
    """Bundle of live components the API surface dispatches to.

    Production wiring (the launcher) constructs everything; tests can
    pass mocks. Attributes are typed loosely (``Any``) so the API code
    does not impose imports on the caller -- the runtime contract is
    documented per attribute.
    """

    # Configuration -----------------------------------------------------
    config: Config | None = None
    save_config: Callable[[Config], Awaitable[None] | None] | None = None

    # Setup-state inputs ------------------------------------------------
    lims_reachable: bool = True
    keyring_password_present: bool = True
    lims_reason: str | None = None

    # Components --------------------------------------------------------
    controller: Any = None
    validator: Any = None
    plugin_host: Any = None
    cache_creation: Any = None
    lims_client: Any = None
    nas_sync: Any = None
    session_store: Any = None
    ingest_writer: Any = None
    staging_watcher: Any = None

    # Audit / pub-sub ---------------------------------------------------
    audit_channel: AuditChannel | None = None
    last_audit_at: str | None = None

    # Health snapshot probes -------------------------------------------
    nas_sync_snapshot: Callable[[], dict[str, Any]] | None = None
    session_store_snapshot: Callable[[], dict[str, Any]] | None = None
    registered_plugin_count: int = 0
    plugin_host_status: str = "ok"

    # Setup probes ------------------------------------------------------
    lims_probe: Callable[..., Any] | None = None
    equipment_probe: Callable[..., Any] | None = None
    autostart_toggle: Callable[[bool], Any] | None = None

    # Background tasks --------------------------------------------------
    audit_task: asyncio.Task[None] | None = field(default=None, repr=False)


# ---------------------------------------------------------------------------
# create_app
# ---------------------------------------------------------------------------


def create_app(
    *,
    config: Config | None = None,
    dependencies: AppDependencies | None = None,
    audit_interval_seconds: float = float(AUDIT_REFRESH_SECONDS),
    start_audit_task: bool = False,
) -> FastAPI:
    """Build the FastAPI app. Backend Spec §4.6.

    ``config``: optional pre-loaded ``config.yaml``; if ``dependencies``
    is supplied this is ignored. ``dependencies``: a fully-configured
    :class:`AppDependencies` (production launcher uses this).
    ``audit_interval_seconds``: how often the background audit task
    runs; tests can pass a small value to exercise the loop.
    ``start_audit_task``: if True the lifespan handler launches the
    audit task; defaults to False so tests don't accumulate tasks.
    """
    deps = dependencies if dependencies is not None else AppDependencies(config=config)
    if deps.audit_channel is None:
        deps.audit_channel = AuditChannel()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.dependencies = deps
        if start_audit_task and deps.validator is not None:
            deps.audit_task = asyncio.create_task(
                _audit_loop(deps, audit_interval_seconds),
                name="exlab-audit-loop",
            )
        try:
            yield
        finally:
            if deps.audit_task is not None and not deps.audit_task.done():
                deps.audit_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await deps.audit_task
            if deps.audit_channel is not None:
                deps.audit_channel.close()

    app = FastAPI(title="ExLab-Wizard", version=__version__, lifespan=lifespan)
    app.state.dependencies = deps

    api_v1 = APIRouter(prefix="/api/v1")
    api_v1.include_router(build_sessions_router())
    api_v1.include_router(build_operations_router())
    api_v1.include_router(build_problems_router())
    api_v1.include_router(build_config_router())
    api_v1.include_router(build_browse_router())
    api_v1.include_router(build_health_router())
    api_v1.include_router(build_setup_router())
    # Staging router is orchestrator-only -- mounted unconditionally so the
    # endpoints surface a structured 503 with code ``orchestrator_disabled``
    # when ``config.orchestrator.enabled`` is False (Backend Spec §13.7,
    # §13.8). The router itself enforces the gate so a future deployment
    # toggling the flag at runtime works without remounting routes.
    api_v1.include_router(build_staging_router())
    app.include_router(api_v1)

    register_exception_handlers(app)
    return app


# ---------------------------------------------------------------------------
# Audit loop
# ---------------------------------------------------------------------------


async def _audit_loop(deps: AppDependencies, interval_seconds: float) -> None:
    """Background task that re-runs the validator audit every interval.

    Diffs the new findings against the previous snapshot and publishes
    the delta on the audit channel.
    """
    last: list[Any] = []
    try:
        while True:
            await asyncio.sleep(interval_seconds)
            try:
                findings = await asyncio.to_thread(
                    deps.validator.audit, {"kind": "all"}
                )
            except Exception as exc:
                _log.warning("audit pass failed: %s", exc)
                continue
            audit_at = datetime.now(tz=UTC).isoformat()
            deps.last_audit_at = audit_at
            added, removed, changed = _diff_findings(last, findings)
            if deps.audit_channel is not None:
                if not last:
                    await deps.audit_channel.publish_snapshot(findings, audit_at)
                else:
                    await deps.audit_channel.publish_delta(
                        added=added, removed=removed, changed=changed, audit_at=audit_at
                    )
            last = list(findings)
    except asyncio.CancelledError:
        raise


def _diff_findings(
    previous: list[Any], current: list[Any]
) -> tuple[list[Any], list[Any], list[Any]]:
    """Return ``(added, removed, changed)`` keyed on ``(rule, offending_path)``.

    The §11.8 contract: a finding is identified by the pair; ``changed``
    catches the case where the rule still fires but the matched_token /
    detail differ.
    """
    prev_map = {(f.rule, f.offending_path): f for f in previous}
    curr_map = {(f.rule, f.offending_path): f for f in current}
    added = [f for k, f in curr_map.items() if k not in prev_map]
    removed = [f for k, f in prev_map.items() if k not in curr_map]
    changed: list[Any] = []
    for k, f_curr in curr_map.items():
        f_prev = prev_map.get(k)
        if f_prev is None:
            continue
        if (f_prev.matched_token, f_prev.rule_detail) != (
            f_curr.matched_token,
            f_curr.rule_detail,
        ):
            changed.append(f_curr)
    return added, removed, changed
