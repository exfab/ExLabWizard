"""``/config`` router. Backend Spec §4.6.1, §4.9.

Endpoints:

* ``GET /config`` -- return the current ``config.yaml`` (always
  available; secrets stripped).
* ``PUT /config`` -- validate + persist new config; re-evaluate setup
  state.

Both endpoints are exempt from the setup-state gate by design (Backend
Spec §4.9.2: the operator needs a way to fix an incomplete config).
"""

from __future__ import annotations

import inspect
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict

from exlab_wizard.api._dependencies import lims_password_present, require_deps
from exlab_wizard.config.models import Config, EquipmentConfig, config_with_equipment_appended
from exlab_wizard.constants import SetupState
from exlab_wizard.errors import ConfigError
from exlab_wizard.logging import get_logger
from exlab_wizard.paths import (
    evaluate_setup_state,
    setup_state_missing,
    setup_state_next_action,
)

__all__ = [
    "ConfigUpdateResponse",
    "EquipmentAppendResponse",
    "build_config_router",
]

_log = get_logger(__name__)


class ConfigUpdateResponse(BaseModel):
    """``PUT /config`` response with the new setup state."""

    model_config = ConfigDict(extra="forbid")

    state: str
    missing: list[dict[str, str]]
    next_action: str | None
    ready: bool


class EquipmentAppendResponse(BaseModel):
    """``POST /config/equipment`` response: the new setup state + appended id.

    Redesign §6: the Add-Equipment wizard posts a single new
    ``EquipmentConfig``; the router validates it, persists, and
    re-evaluates the setup-incomplete state.
    """

    model_config = ConfigDict(extra="forbid")

    appended_id: str
    state: str
    missing: list[dict[str, str]]
    next_action: str | None
    ready: bool


def build_config_router() -> APIRouter:
    """Construct the ``/config`` router. Routes are always available."""
    router = APIRouter(tags=["config"])

    @router.get("/config", response_model=Config)
    async def get_config(request: Request) -> Config:
        deps = require_deps(request)
        config = getattr(deps, "config", None)
        # Empty default config is the right shape when no config.yaml
        # exists on disk. Frontend treats this the same as
        # INCOMPLETE_NO_CONFIG. The Config model carries no in-band
        # secrets (passwords live in the keyring) so no redaction pass
        # is needed here.
        return config if config is not None else Config()

    @router.put("/config", response_model=ConfigUpdateResponse)
    async def put_config(request: Request, body: Config) -> ConfigUpdateResponse:
        deps = require_deps(request)
        # Persist via the host-supplied saver (loader.save_config in
        # production). Tests can substitute a no-op.
        saver = getattr(deps, "save_config", None)
        if saver is not None:
            await _await_or_call(saver, body)
        deps.config = body
        # Re-evaluate setup state with the new config.
        state = evaluate_setup_state(
            deps.config,
            lims_reachable=getattr(deps, "lims_reachable", True),
            keyring_password_present=lims_password_present(deps),
        )
        return ConfigUpdateResponse(
            state=state.value,
            missing=setup_state_missing(state, deps.config),
            next_action=setup_state_next_action(state),
            ready=state is SetupState.READY,
        )

    @router.post(
        "/config/equipment",
        response_model=EquipmentAppendResponse,
    )
    async def append_equipment(request: Request, body: EquipmentConfig) -> EquipmentAppendResponse:
        """Append a validated ``EquipmentConfig`` to the live config.

        Redesign §6: the Add-Equipment wizard's confirm step posts here.
        Duplicate IDs are rejected with a structured error per §10.
        """
        deps = require_deps(request)
        try:
            new_config = config_with_equipment_appended(getattr(deps, "config", None), body)
        except ConfigError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "equipment_id_conflict", "message": str(exc)},
            ) from exc
        saver = getattr(deps, "save_config", None)
        if saver is not None:
            await _await_or_call(saver, new_config)
        deps.config = new_config
        state = evaluate_setup_state(
            deps.config,
            lims_reachable=getattr(deps, "lims_reachable", True),
            keyring_password_present=lims_password_present(deps),
        )
        return EquipmentAppendResponse(
            appended_id=body.id,
            state=state.value,
            missing=setup_state_missing(state, deps.config),
            next_action=setup_state_next_action(state),
            ready=state is SetupState.READY,
        )

    return router


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _await_or_call(callable_: Any, *args: Any) -> Any:
    """Invoke a saver that may be sync or async."""
    result = callable_(*args)
    if inspect.isawaitable(result):
        return await result
    return result
