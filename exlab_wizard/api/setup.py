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

from exlab_wizard.config.models import (
    EquipmentConfig,
    EquipmentTransport,
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
    """``POST /setup/test-equipment`` request body."""

    model_config = ConfigDict(extra="forbid")

    equipment: EquipmentConfig | None = None
    equipment_id: str | None = None


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

    The dependency object exposes ``config`` and a
    ``lims_reachable`` boolean (cached at startup; the
    ``POST /setup/test-lims`` endpoint refreshes it).
    """
    return evaluate_setup_state(
        deps.config,
        lims_reachable=getattr(deps, "lims_reachable", True),
        keyring_password_present=getattr(deps, "keyring_password_present", True),
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
        deps = _require_deps(request)
        state = compute_setup_state(deps)
        return SetupStatusResponse(
            state=state.value,
            missing=setup_state_missing(state, deps.config),
            next_action=setup_state_next_action(state),
            ready=state is SetupState.READY,
        )

    @router.post("/test-lims", response_model=TestResult)
    async def test_lims(request: Request, body: TestLIMSRequest | None = None) -> TestResult:
        deps = _require_deps(request)
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
    async def test_equipment(
        request: Request, body: TestEquipmentRequest | None = None
    ) -> TestResult:
        deps = _require_deps(request)
        probe = getattr(deps, "equipment_probe", None)
        if probe is None:
            return TestResult(
                ok=False,
                reason="equipment probe is not wired on this app instance",
            )
        equipment = _resolve_equipment(deps, body)
        if equipment is None:
            return TestResult(ok=False, reason="no matching equipment configuration")
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
        deps = _require_deps(request)
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


def _require_deps(request: Request) -> Any:
    """Return the app's dependencies, or raise a clear 503.

    The ``/setup/*`` endpoints assume the lifespan handler ran; if
    ``app.state.dependencies`` is missing it indicates a wiring bug
    (test fixture forgot to attach deps), not an operator-correctable
    state, so we surface it as 500.
    """
    deps = getattr(request.app.state, "dependencies", None)
    if deps is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "internal_error",
                "message": "app dependencies not initialized",
            },
        )
    return deps


def _resolve_equipment(deps: Any, body: TestEquipmentRequest | None) -> EquipmentConfig | None:
    """Pick the equipment to probe.

    ``body.equipment`` wins when supplied (Settings UI's pre-save
    "Test connection" affordance). Otherwise ``body.equipment_id``
    resolves through the loaded config. Otherwise the first configured
    equipment is used so the endpoint is callable with an empty body.
    """
    if body is not None and body.equipment is not None:
        return body.equipment
    config = getattr(deps, "config", None)
    if config is None or not getattr(config, "equipment", None):
        return None
    if body is not None and body.equipment_id:
        for entry in config.equipment:
            if entry.id == body.equipment_id:
                return entry
        return None
    return config.equipment[0]


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


# Internal types kept here so the router declaration above type-checks
# without requiring the caller to import EquipmentTransport directly.
__all_internal__ = (EquipmentTransport,)
