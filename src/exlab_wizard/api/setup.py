"""Setup-state gate + ``/setup/*`` endpoints. Backend Spec §4.6, §4.9.

Two responsibilities live here:

1. The **setup-state gate** -- a per-request dependency that consults
   :func:`paths.evaluate_setup_state` and returns 503 with
   ``code: "setup_incomplete"`` for routes that need a complete
   ``config.yaml`` (creation, browse, problems). Routes that must
   remain available during onboarding (``/setup/*``, ``/config``,
   ``/health``) skip the dependency.
2. The **setup endpoints** -- ``GET /setup/status``,
   ``POST /setup/test-lims``, ``POST /setup/test-equipment``,
   ``POST /setup/autostart``. These are the wizard's "diagnostics"
   surface and must work in any setup state.

Per Backend §4.9.4, ``INCOMPLETE_LIMS_UNREACHABLE`` is a soft block:
the gate treats it as ``READY`` for endpoint-gating purposes; the
``/setup/status`` endpoint surfaces the soft state separately so the
banner can render.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from exlab_wizard.api._dependencies import (
    lims_password_present,
    nas_remote_available,
    require_deps,
)
from exlab_wizard.config.models import (
    EquipmentConfig,
    LIMSConfig,
)
from exlab_wizard.constants import SetupState
from exlab_wizard.logging import get_logger
from exlab_wizard.paths import (
    evaluate_setup_state,
    setup_state_missing,
    setup_state_next_action,
)

__all__ = [
    "AutostartRequest",
    "EquipmentTestRequest",
    "LIMSTestRequest",
    "ProbeResult",
    "SetupStatusResponse",
    "TestEquipmentRequest",
    "TestLIMSRequest",
    "TestResult",
    "build_setup_router",
    "compute_setup_state",
    "is_creation_blocked",
    "setup_state_gate",
]

_log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Pydantic request / response models
# ---------------------------------------------------------------------------


class SetupStatusResponse(BaseModel):
    """``GET /setup/status`` response. Backend Spec §4.9.3."""

    model_config = ConfigDict(extra="forbid")

    state: str
    missing: list[dict[str, str]] = Field(default_factory=list)
    next_action: str | None = None
    ready: bool


class LIMSTestRequest(BaseModel):
    """``POST /setup/test-lims`` request body.

    Either reference the currently-configured LIMS settings (no body
    fields) or supply a ``LIMSConfig`` candidate to test before save.

    Class is named ``LIMSTestRequest`` (rather than ``TestLIMSRequest``)
    so pytest does not pick it up as a test class on collection.
    """

    model_config = ConfigDict(extra="forbid")

    lims: LIMSConfig | None = None
    password: str | None = None


class EquipmentTestRequest(BaseModel):
    """``POST /setup/test-equipment`` request body.

    Rclone-only NAS sync migration (2026-05-26). The pre-save
    body-equipment path was removed: the probe needs the keyring
    password, which is looked up by equipment id, and a candidate
    equipment that hasn't been saved yet cannot have a keyring entry
    by definition. Callers must therefore reference an already-saved
    equipment by id.
    """

    model_config = ConfigDict(extra="forbid")

    equipment_id: str


class ProbeResult(BaseModel):
    """Common ``ok``/``reason`` payload for the diagnostics endpoints."""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    reason: str | None = None
    latency_ms: int | None = None


# Backwards-compatible aliases for legacy imports. Kept as a separate
# binding rather than via assignment so the API documentation reflects
# the canonical names above.
TestLIMSRequest = LIMSTestRequest
TestEquipmentRequest = EquipmentTestRequest
TestResult = ProbeResult


class AutostartRequest(BaseModel):
    """``POST /setup/autostart`` request body. Backend Spec §4.9.5 step 0."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool


class AutostartResult(BaseModel):
    """``POST /setup/autostart`` response."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool
    registered: bool


# ---------------------------------------------------------------------------
# Setup-state evaluation
# ---------------------------------------------------------------------------


def compute_setup_state(deps: Any) -> SetupState:
    """Evaluate the §4.9.1 state for the app's current dependencies.

    The dependency object exposes ``config``, a ``lims_reachable``
    boolean (cached at startup; the ``POST /setup/test-lims`` endpoint
    refreshes it), and (rclone.conf NAS-sync migration) a
    ``nas_remote_available`` predicate that answers whether a named
    rclone remote is present in rclone.conf.
    """
    return evaluate_setup_state(
        deps.config,
        lims_reachable=getattr(deps, "lims_reachable", True),
        keyring_password_present=lims_password_present(deps),
        nas_remote_available=lambda remote: nas_remote_available(deps, remote),
    )


def is_creation_blocked(state: SetupState) -> bool:
    """Return True when ``state`` should gate creation flows.

    Per §4.9.4 the soft block (``INCOMPLETE_LIMS_UNREACHABLE``) does
    NOT gate creation -- the operator may be on an offline machine
    using the cached project list. ``READY`` obviously does not gate.
    """
    return state not in (SetupState.READY, SetupState.INCOMPLETE_LIMS_UNREACHABLE)


def setup_state_gate(request: Request) -> None:
    """FastAPI dependency that gates a route on setup state.

    Looks up the app's bound :class:`AppDependencies`, evaluates the
    setup state, and raises 503 with the §4.9.2 envelope when the
    state is any non-soft INCOMPLETE_*. The dependency itself is a
    plain function so it can be overridden in tests via
    ``app.dependency_overrides``.
    """
    deps = getattr(request.app.state, "dependencies", None)
    if deps is None:
        # No app dependencies wired -- treat as READY (e.g. unit tests
        # constructing a bare FastAPI). The gate is opt-in; routes that
        # need it consume this dependency explicitly.
        return
    state = compute_setup_state(deps)
    if not is_creation_blocked(state):
        return
    missing = setup_state_missing(state, deps.config)
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "code": "setup_incomplete",
            "message": "setup is incomplete; complete onboarding before using this endpoint",
            "state": state.value,
            "missing": missing,
        },
    )


# ---------------------------------------------------------------------------
# Router construction
# ---------------------------------------------------------------------------


def build_setup_router() -> APIRouter:
    """Construct the ``/setup/*`` router. Always-available endpoints."""
    router = APIRouter(prefix="/setup", tags=["setup"])

    @router.get("/status", response_model=SetupStatusResponse)
    async def get_setup_status(request: Request) -> SetupStatusResponse:
        deps = require_deps(request)
        state = compute_setup_state(deps)
        return SetupStatusResponse(
            state=state.value,
            missing=setup_state_missing(state, deps.config),
            next_action=setup_state_next_action(state),
            ready=state is SetupState.READY,
        )

    @router.post("/test-lims", response_model=TestResult)
    async def test_lims(request: Request, body: TestLIMSRequest | None = None) -> TestResult:
        deps = require_deps(request)
        probe = getattr(deps, "lims_probe", None)
        if probe is None:
            return TestResult(
                ok=False,
                reason="LIMS probe is not wired on this app instance",
            )
        try:
            result = await _await_or_call(probe, body)
        except Exception as exc:
            return TestResult(ok=False, reason=str(exc))
        if isinstance(result, TestResult):
            return result
        if isinstance(result, dict):
            return _coerce_probe_dict(result)
        return TestResult(ok=bool(result))

    @router.post("/test-equipment", response_model=TestResult)
    async def test_equipment(request: Request, body: TestEquipmentRequest) -> TestResult:
        deps = require_deps(request)
        probe = getattr(deps, "equipment_probe", None)
        if probe is None:
            return TestResult(
                ok=False,
                reason="equipment probe is not wired on this app instance",
            )
        equipment = _resolve_equipment(deps, body)
        if equipment is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "equipment_not_found",
                    "message": f"no equipment with id {body.equipment_id!r}",
                },
            )
        try:
            result = await _await_or_call(probe, equipment)
        except Exception as exc:
            return TestResult(ok=False, reason=str(exc))
        if isinstance(result, TestResult):
            return result
        if isinstance(result, dict):
            return _coerce_probe_dict(result)
        return TestResult(ok=bool(result))

    @router.post("/autostart", response_model=AutostartResult)
    async def set_autostart(request: Request, body: AutostartRequest) -> AutostartResult:
        deps = require_deps(request)
        toggle = getattr(deps, "autostart_toggle", None)
        if toggle is None:
            # No tray module wired (e.g. integration tests). Echo the
            # operator's choice; persistence is handled by the caller.
            return AutostartResult(enabled=body.enabled, registered=body.enabled)
        try:
            registered = await _await_or_call(toggle, body.enabled)
        except Exception as exc:
            _log.warning("autostart toggle failed: %s", exc)
            return AutostartResult(enabled=body.enabled, registered=False)
        return AutostartResult(enabled=body.enabled, registered=bool(registered))

    return router


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _coerce_probe_dict(payload: dict[str, Any]) -> ProbeResult:
    """Build a ``ProbeResult`` from a probe's plain-dict return value.

    Wired through a helper so the ``ok`` field gets coerced to ``bool``
    explicitly (probes sometimes return truthy non-bool values such as
    ``1`` or ``"yes"``); this keeps mypy happy and matches the field's
    declared type.
    """
    reason = payload.get("reason")
    latency_ms = payload.get("latency_ms")
    return ProbeResult(
        ok=bool(payload.get("ok")),
        reason=str(reason) if reason is not None else None,
        latency_ms=int(latency_ms) if latency_ms is not None else None,
    )


def _resolve_equipment(deps: Any, body: TestEquipmentRequest) -> EquipmentConfig | None:
    """Resolve the equipment to probe by id through ``deps.config``.

    Rclone-only NAS sync migration (2026-05-26). The endpoint requires
    ``equipment_id`` -- the probe needs the keyring password, which is
    keyed by equipment id, so a body-equipment candidate that has not
    been saved yet cannot satisfy the probe. Returns ``None`` when the
    config is unloaded, the equipment list is empty, or the id does
    not match a registered entry.
    """
    config = getattr(deps, "config", None)
    if config is None or not getattr(config, "equipment", None):
        return None
    for entry in config.equipment:
        if entry.id == body.equipment_id:
            return entry
    return None


async def _await_or_call(callable_: Callable[..., Any], *args: Any) -> Any:
    """Invoke a probe that may be sync or async; await the result.

    The probes are typed loosely on the dependencies object so tests
    can pass simple lambdas. We accept either a coroutine function or
    a plain callable.
    """
    result = callable_(*args)
    if inspect.isawaitable(result):
        return await result
    return result
