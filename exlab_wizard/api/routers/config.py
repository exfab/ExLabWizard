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

from exlab_wizard.config.models import Config
from exlab_wizard.constants import SetupState
from exlab_wizard.logging import get_logger
from exlab_wizard.paths import (
    evaluate_setup_state,
    setup_state_missing,
    setup_state_next_action,
)

__all__ = ["ConfigUpdateResponse", "build_config_router"]

_log = get_logger(__name__)

# Field paths (dotted notation) that must never appear in a GET /config
# response. The Pydantic model never stores secrets directly (passwords
# live in the keyring), but the redaction list is encoded here so adding
# a future secret field is a one-line change.
_REDACTED_FIELDS: frozenset[str] = frozenset()


class ConfigUpdateResponse(BaseModel):
    """``PUT /config`` response with the new setup state."""

    model_config = ConfigDict(extra="forbid")

    state: str
    missing: list[dict[str, str]]
    next_action: str | None
    ready: bool


def build_config_router() -> APIRouter:
    """Construct the ``/config`` router. Routes are always available."""
    router = APIRouter(tags=["config"])

    @router.get("/config", response_model=Config)
    async def get_config(request: Request) -> Config:
        deps = _require_deps(request)
        config = getattr(deps, "config", None)
        if config is None:
            # Empty default config is the right shape when no config.yaml
            # exists on disk. Frontend treats this the same as
            # INCOMPLETE_NO_CONFIG.
            return Config()
        return _redact(config)

    @router.put("/config", response_model=ConfigUpdateResponse)
    async def put_config(request: Request, body: Config) -> ConfigUpdateResponse:
        deps = _require_deps(request)
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
            keyring_password_present=getattr(deps, "keyring_password_present", True),
        )
        return ConfigUpdateResponse(
            state=state.value,
            missing=setup_state_missing(state, deps.config),
            next_action=setup_state_next_action(state),
            ready=state is SetupState.READY,
        )

    return router


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_deps(request: Request) -> Any:
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


def _redact(config: Config) -> Config:
    """Return ``config`` with secret fields blanked out.

    The current ``Config`` model carries no in-band secrets (LIMS / NAS
    passwords live in the keyring), so this is a no-op pass-through.
    The function is kept so the redaction policy lives in one place;
    when future fields land they are added to :data:`_REDACTED_FIELDS`
    and zeroed here.
    """
    if not _REDACTED_FIELDS:
        return config
    return config


async def _await_or_call(callable_: Any, *args: Any) -> Any:
    """Invoke a saver that may be sync or async."""
    result = callable_(*args)
    if inspect.isawaitable(result):
        return await result
    return result
