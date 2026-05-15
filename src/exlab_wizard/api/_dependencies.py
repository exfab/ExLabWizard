"""Shared FastAPI dependency helpers for the wizard's HTTP routers.

The lifespan handler attaches an ``AppDependencies`` instance to
``request.app.state.dependencies`` once the controller, validator,
sync queue, etc. have all been initialized. Routers that require any
of those collaborators historically open-coded the same
``getattr(...)`` + ``HTTPException`` block; this module collapses the
six near-identical copies into one helper per assertion.

Per-spec semantics (§4.6.3): when a wizard collaborator is not yet
wired, the request fails with ``503 SERVICE_UNAVAILABLE`` -- the
wizard cannot satisfy the request right now even though the request
itself is valid -- carrying the structured ``internal_error`` envelope
the rest of the API uses for non-validation failures.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request, status

__all__ = [
    "require_controller",
    "require_deps",
]


def require_deps(request: Request) -> Any:
    """Return ``app.state.dependencies`` or raise a structured 503.

    A missing dependency object means the lifespan handler did not run
    (e.g. a test fixture that forgot to attach deps); the HTTP layer
    has nothing to dispatch to so we return ``service_unavailable`` to
    the operator.
    """
    deps = getattr(request.app.state, "dependencies", None)
    if deps is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "internal_error",
                "message": "app dependencies are not initialized",
            },
        )
    return deps


def require_controller(request: Request) -> Any:
    """Return ``deps.controller`` or raise a structured 503.

    Used by the routes that drive sessions: when the controller is
    absent, no creation pipeline can run, so the request is
    service-unavailable.
    """
    deps = getattr(request.app.state, "dependencies", None)
    controller = getattr(deps, "controller", None) if deps else None
    if controller is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "internal_error",
                "message": "controller is not initialized",
            },
        )
    return controller
