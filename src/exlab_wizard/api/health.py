"""``GET /api/v1/health`` rollup. Backend Spec §4.6.3.

Returns a component-health snapshot regardless of setup state. The
endpoint is the launcher's "is the server up" probe and the Settings
dialog's diagnostics surface. Per spec the HTTP status is always 200;
the top-level ``status`` field is the contract.

The component statuses are read from the bound :class:`AppDependencies`
where available (validator, NAS sync queue, plugin registry, session
store). Components that are not wired in a test or stub scenario report
``status == "ok"`` with the component-specific reason field absent.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict

from exlab_wizard import __version__
from exlab_wizard.api.setup import compute_setup_state
from exlab_wizard.constants import (
    CREATION_JSON_VERSION,
    INGEST_JSON_VERSION,
    README_FIELDS_JSON_VERSION,
)
from exlab_wizard.logging import get_logger

__all__ = ["HealthResponse", "build_health_router"]

_log = get_logger(__name__)


class HealthResponse(BaseModel):
    """``GET /health`` response body. Backend Spec §4.6.3."""

    model_config = ConfigDict(extra="forbid")

    status: str
    version: str
    schema_versions: dict[str, str]
    components: dict[str, dict[str, Any]]
    setup_state: str


def build_health_router() -> APIRouter:
    """Construct the health router. Always available."""
    router = APIRouter(tags=["health"])

    @router.get("/health", response_model=HealthResponse)
    async def get_health(request: Request) -> HealthResponse:
        deps = getattr(request.app.state, "dependencies", None)
        components = _component_rollup(deps)
        top = _top_level_status(components)
        setup_state_value = compute_setup_state(deps).value if deps is not None else "ready"
        return HealthResponse(
            status=top,
            version=__version__,
            schema_versions={
                "creation_json": CREATION_JSON_VERSION,
                "readme_fields_json": README_FIELDS_JSON_VERSION,
                "ingest_json": INGEST_JSON_VERSION,
            },
            components=components,
            setup_state=setup_state_value,
        )

    return router


def _component_rollup(deps: Any) -> dict[str, dict[str, Any]]:
    """Build the §4.6.3 components block from the live dependencies."""
    return {
        "validator": _validator_health(deps),
        "nas_sync": _nas_sync_health(deps),
        "lims": _lims_health(deps),
        "plugin_host": _plugin_host_health(deps),
        "session_store": _session_store_health(deps),
    }


def _validator_health(deps: Any) -> dict[str, Any]:
    last_audit_at = getattr(deps, "last_audit_at", None) if deps is not None else None
    body: dict[str, Any] = {"status": "ok"}
    if last_audit_at:
        body["last_audit_at"] = last_audit_at
    return body


def _nas_sync_health(deps: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"status": "ok", "queue_depth": 0, "in_flight": 0}
    if deps is None:
        return body
    snapshot = getattr(deps, "nas_sync_snapshot", None)
    if callable(snapshot):
        try:
            payload = snapshot()
        except Exception as exc:
            _log.warning("nas_sync snapshot failed: %s", exc)
            return {"status": "warn", "reason": str(exc), "queue_depth": 0, "in_flight": 0}
        if isinstance(payload, dict):
            body.update(
                {
                    "queue_depth": int(payload.get("queue_depth", 0)),
                    "in_flight": int(payload.get("in_flight", 0)),
                }
            )
            if "status" in payload:
                body["status"] = payload["status"]
            if "reason" in payload:
                body["reason"] = payload["reason"]
    return body


def _lims_health(deps: Any) -> dict[str, Any]:
    if deps is None:
        return {"status": "ok"}
    if not getattr(deps, "lims_reachable", True):
        return {
            "status": "warn",
            "reason": "unreachable; using cache",
        }
    reason = getattr(deps, "lims_reason", None)
    body: dict[str, Any] = {"status": "ok"}
    if reason:
        body["reason"] = str(reason)
    return body


def _plugin_host_health(deps: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"status": "ok", "registered_plugins": 0}
    if deps is None:
        return body
    plugin_count = getattr(deps, "registered_plugin_count", None)
    if isinstance(plugin_count, int):
        body["registered_plugins"] = plugin_count
    plugin_status = getattr(deps, "plugin_host_status", None)
    if isinstance(plugin_status, str):
        body["status"] = plugin_status
    return body


def _session_store_health(deps: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"status": "ok", "active_sessions": 0, "input_required": 0}
    if deps is None:
        return body
    snapshot = getattr(deps, "session_store_snapshot", None)
    if callable(snapshot):
        try:
            payload = snapshot()
        except Exception as exc:
            _log.warning("session_store snapshot failed: %s", exc)
            return {"status": "warn", "reason": str(exc), "active_sessions": 0, "input_required": 0}
        if isinstance(payload, dict):
            body["active_sessions"] = int(payload.get("active_sessions", 0))
            body["input_required"] = int(payload.get("input_required", 0))
    return body


def _top_level_status(components: dict[str, dict[str, Any]]) -> str:
    """Aggregate per-component statuses into the §4.6.3 top-level value.

    Top-level ``status`` is the most severe of the components: ``ok``
    when every component is ok, ``warn`` when any component is warn
    (and none error), ``error`` when any component is error.
    """
    severities = [c.get("status", "ok") for c in components.values()]
    if "error" in severities:
        return "error"
    if "warn" in severities:
        return "warn"
    return "ok"
