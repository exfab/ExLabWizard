"""Error envelope helpers + FastAPI exception handlers. Backend Spec §4.6.3.

Every error response across the API uses the §4.6.3 JSON shape::

    {
      "error": {
        "code": "validation_failed",
        "message": "Operator field cannot be empty.",
        "field": "operator",
        "details": { "min_length": 1 },
        "trace_id": "abc123def456"
      }
    }

Required: ``code`` (stable string identifier; this is what client code
branches on), ``message`` (human-readable). Optional: ``field``
(field-level validation errors), ``details`` (free-form structured
detail), ``trace_id`` (echoed from the request's ``X-Trace-Id`` header
if present, else server-generated; correlates with the central app log).

The full ``code`` enum table is in §4.6.3; :data:`ERROR_CODES` mirrors
it as a closed string set so adding a new code requires updating both
the spec section and this module in the same change.
"""

from __future__ import annotations

import secrets
from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError as PydanticValidationError

from exlab_wizard.errors import (
    ConfigError,
    KeyringUnavailableError,
    SchemaMajorMismatchError,
    SetupIncompleteError,
    TemplateCoreFieldRedeclaredError,
    TemplateLoadError,
)
from exlab_wizard.errors import (
    ValidationError as ExLabValidationError,
)
from exlab_wizard.logging import get_logger

__all__ = [
    "ERROR_CODES",
    "EW_TRACE_ID_HEADER",
    "build_error_envelope",
    "error_response",
    "extract_or_create_trace_id",
    "register_exception_handlers",
]

_log = get_logger(__name__)

# HTTP header that carries the per-request trace id. Mirrors §4.6.3's
# "echoed back from the request's X-Trace-Id header if present" clause.
EW_TRACE_ID_HEADER = "X-Trace-Id"

# Closed string set of error codes per the §4.6.3 table. Each value
# matches a row in the spec's "code | HTTP status | Emitted by" table.
ERROR_CODES: frozenset[str] = frozenset(
    {
        "setup_incomplete",
        "shutting_down",
        "validation_failed",
        "plugin_variable_validation_failed",
        "template_load_error",
        "template_core_field_redeclared",
        "lims_unreachable",
        "keyring_unavailable",
        "session_not_found",
        "session_already_completed",
        "nas_sync_failed",
        "schema_major_mismatch",
        "equipment_id_invalid",
        "field_too_long",
        "disk_space_insufficient",
        "plugin_host_unavailable",
        "internal_error",
    }
)


def extract_or_create_trace_id(request: Request | None) -> str:
    """Return the request's ``X-Trace-Id`` header, else a fresh hex id.

    Server-generated ids use 12 hex characters of cryptographic
    randomness (``secrets.token_hex(6)``); plenty of bits for log
    correlation in a single-user desktop app.
    """
    if request is not None:
        header = request.headers.get(EW_TRACE_ID_HEADER)
        if header:
            return header
    return secrets.token_hex(6)


def build_error_envelope(
    *,
    code: str,
    message: str,
    field: str | None = None,
    details: Mapping[str, Any] | None = None,
    trace_id: str | None = None,
) -> dict[str, Any]:
    """Build the §4.6.3 envelope dict.

    ``code`` is validated against :data:`ERROR_CODES`; an unknown code
    is replaced with ``"internal_error"`` and logged at WARN so the
    client always gets a known discriminator.
    """
    if code not in ERROR_CODES:
        _log.warning("unknown error code %r; substituting 'internal_error'", code)
        code = "internal_error"
    error: dict[str, Any] = {
        "code": code,
        "message": message,
    }
    if field is not None:
        error["field"] = field
    if details is not None:
        error["details"] = dict(details)
    if trace_id is not None:
        error["trace_id"] = trace_id
    return {"error": error}


def error_response(
    *,
    request: Request | None,
    code: str,
    message: str,
    status_code: int,
    field: str | None = None,
    details: Mapping[str, Any] | None = None,
    extra: Mapping[str, Any] | None = None,
) -> JSONResponse:
    """Build a FastAPI JSONResponse carrying the §4.6.3 envelope.

    ``extra`` is merged into the ``error`` block alongside the four
    standard fields. The setup-incomplete handler uses this to attach
    ``state`` and ``missing`` per §4.9.2.
    """
    trace_id = extract_or_create_trace_id(request)
    envelope = build_error_envelope(
        code=code,
        message=message,
        field=field,
        details=details,
        trace_id=trace_id,
    )
    if extra:
        envelope["error"].update(extra)
    headers = {EW_TRACE_ID_HEADER: trace_id}
    return JSONResponse(envelope, status_code=status_code, headers=headers)


# ---------------------------------------------------------------------------
# Exception -> envelope translators
# ---------------------------------------------------------------------------


def _exlab_validation_handler(request: Request, exc: ExLabValidationError) -> JSONResponse:
    """Translate :class:`exlab_wizard.errors.ValidationError` to envelope.

    The controller raises ValidationError with a structured envelope as
    the first arg (see ``CreationController._format_error``). When the
    envelope is present we use its fields directly; otherwise we fall
    back to the exception's string form.
    """
    payload: dict[str, Any] = {}
    if exc.args and isinstance(exc.args[0], dict):
        payload = dict(exc.args[0])
    code = payload.get("code", "validation_failed")
    return error_response(
        request=request,
        code=str(code),
        message=str(payload.get("message", str(exc))),
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        field=payload.get("field"),
        details=payload.get("details"),
    )


def _pydantic_validation_handler(
    request: Request, exc: PydanticValidationError | RequestValidationError
) -> JSONResponse:
    """Translate Pydantic validation errors to the §4.6.3 envelope.

    FastAPI raises :class:`fastapi.exceptions.RequestValidationError`
    for body / query mismatches; we translate the first error into the
    envelope shape and stuff every error's path into ``details.errors``.
    """
    errors = list(exc.errors())
    first = errors[0] if errors else {}
    field_loc = first.get("loc", ())
    # Drop the "body"/"query"/etc prefix Pydantic adds; the API surface
    # only cares about the field name from the body itself.
    field_name: str | None
    if isinstance(field_loc, (list, tuple)) and field_loc:
        if field_loc[0] in ("body", "query", "path", "header"):
            field_loc = tuple(field_loc[1:])
        field_name = ".".join(str(part) for part in field_loc) if field_loc else None
    else:
        field_name = None
    return error_response(
        request=request,
        code="validation_failed",
        message=str(first.get("msg", "request validation failed")),
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        field=field_name,
        details={"errors": [_pydantic_error_to_dict(e) for e in errors]},
    )


def _pydantic_error_to_dict(error: dict[str, Any]) -> dict[str, Any]:
    """Strip Pydantic error dicts of unhashable / large fields."""
    return {
        "loc": list(error.get("loc", ())),
        "msg": error.get("msg"),
        "type": error.get("type"),
    }


def _config_error_handler(request: Request, exc: ConfigError) -> JSONResponse:
    return error_response(
        request=request,
        code="validation_failed",
        message=str(exc),
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
    )


def _keyring_unavailable_handler(
    request: Request, exc: KeyringUnavailableError
) -> JSONResponse:
    return error_response(
        request=request,
        code="keyring_unavailable",
        message=str(exc) or "the OS keyring backend is unavailable",
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    )


def _schema_major_mismatch_handler(
    request: Request, exc: SchemaMajorMismatchError
) -> JSONResponse:
    return error_response(
        request=request,
        code="schema_major_mismatch",
        message=str(exc),
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        details={"expected_major": exc.expected_major, "found": exc.found},
    )


def _setup_incomplete_handler(request: Request, exc: SetupIncompleteError) -> JSONResponse:
    """Translate :class:`SetupIncompleteError` to the §4.9.2 envelope.

    The controller raises this when a creation flow is invoked while
    ``state`` is any ``INCOMPLETE_*`` other than the soft block. The
    envelope adds ``state`` and ``missing`` as extra fields per the
    spec.
    """
    extra: dict[str, Any] = {}
    if exc.args and isinstance(exc.args[0], dict):
        payload = exc.args[0]
        if "state" in payload:
            extra["state"] = payload["state"]
        if "missing" in payload:
            extra["missing"] = list(payload["missing"])
    return error_response(
        request=request,
        code="setup_incomplete",
        message=str(exc) or "setup is incomplete",
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        extra=extra,
    )


def _template_load_error_handler(request: Request, exc: TemplateLoadError) -> JSONResponse:
    code = (
        "template_core_field_redeclared"
        if isinstance(exc, TemplateCoreFieldRedeclaredError)
        else "template_load_error"
    )
    return error_response(
        request=request,
        code=code,
        message=str(exc),
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
    )


def _http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Translate an :class:`HTTPException` into the §4.6.3 envelope.

    Routers raise ``HTTPException(status_code=..., detail=<dict>)`` with
    a ``code``-bearing dict. We hoist those fields onto the envelope so
    every error response is shape-identical.
    """
    detail = exc.detail
    if isinstance(detail, dict) and "code" in detail:
        code = str(detail.get("code", "internal_error"))
        message = str(detail.get("message", str(exc.detail)))
        field = detail.get("field")
        details = detail.get("details")
        extra = {
            k: v
            for k, v in detail.items()
            if k not in {"code", "message", "field", "details"}
        }
        return error_response(
            request=request,
            code=code,
            message=message,
            status_code=exc.status_code,
            field=field,
            details=details,
            extra=extra or None,
        )
    return error_response(
        request=request,
        code=_status_to_code(exc.status_code),
        message=str(detail) if detail is not None else "",
        status_code=exc.status_code,
    )


def _status_to_code(status_code: int) -> str:
    """Best-effort default code for a bare ``HTTPException``."""
    if status_code == 404:
        return "session_not_found"
    if status_code == 409:
        return "session_already_completed"
    if status_code == 503:
        return "setup_incomplete"
    if status_code == 422:
        return "validation_failed"
    return "internal_error"


def _generic_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all 500 handler. Backend Spec §4.6.3 last paragraph.

    The exception's message is stripped from the envelope to avoid
    leaking internals; a server-side log entry retains the full
    traceback. ``trace_id`` is generated and returned to the client so
    support can correlate the report with the log line.
    """
    trace_id = extract_or_create_trace_id(request)
    _log.exception("internal_error trace_id=%s", trace_id)
    return error_response(
        request=request,
        code="internal_error",
        message="internal server error",
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        details={"trace_id": trace_id},
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Attach the §4.6.3 envelope handlers to a FastAPI app.

    Registered in priority order (FastAPI dispatch is most-specific
    first by ``isinstance`` tree; the order below is exhaustive enough
    that the order at registration is mostly cosmetic).
    """
    app.add_exception_handler(SetupIncompleteError, _setup_incomplete_handler)
    app.add_exception_handler(KeyringUnavailableError, _keyring_unavailable_handler)
    app.add_exception_handler(SchemaMajorMismatchError, _schema_major_mismatch_handler)
    app.add_exception_handler(TemplateLoadError, _template_load_error_handler)
    app.add_exception_handler(ConfigError, _config_error_handler)
    app.add_exception_handler(ExLabValidationError, _exlab_validation_handler)
    app.add_exception_handler(RequestValidationError, _pydantic_validation_handler)
    app.add_exception_handler(PydanticValidationError, _pydantic_validation_handler)
    app.add_exception_handler(HTTPException, _http_exception_handler)
    app.add_exception_handler(Exception, _generic_handler)
