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
    "lims_password_present",
    "nas_password_present",
    "require_controller",
    "require_deps",
]


def lims_password_present(deps: Any) -> bool:
    """Return whether a LIMS password is stored -- the repo-wide reader.

    Every surface that asks "is the LIMS keyring password set?" -- the
    settings credential field, the setup-state evaluator, the
    section-completion gate -- routes through here so the default and
    the ``deps is None`` handling stay identical instead of each call
    site open-coding its own ``getattr(deps, "keyring_password_present",
    ...)`` with its own default.

    ``deps`` is typed ``Any`` because callers hold it loosely
    (``AppDependencies`` in production, mocks in tests, ``None`` before
    wiring). A ``None`` or attribute-less ``deps`` means nothing is
    wired yet, so the password cannot be present -- hence ``False``.
    """
    if deps is None:
        return False
    return bool(getattr(deps, "keyring_password_present", False))


def nas_password_present(deps: Any, equipment_id: str) -> bool:
    """Return whether the NAS keyring password is set for ``equipment_id``.

    Rclone-only NAS sync migration (2026-05-26). Mirrors
    :func:`lims_password_present`: every surface that asks "is the NAS
    keyring password set for equipment X?" -- the setup-state evaluator,
    the per-equipment credential field in Settings, the equipment probe
    -- routes through here so the default and the ``deps is None``
    handling stay consistent.

    The presence set is hydrated once at tray boot
    (``deps.nas_password_present``) and mutated by the Settings UI's
    Save / Clear handlers; a missing attribute is treated as "no
    passwords known", which is the correct boot-time default before
    the field is wired.
    """
    if deps is None:
        return False
    present = getattr(deps, "nas_password_present", None)
    if present is None:
        return False
    return equipment_id in present


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
