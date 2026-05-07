"""Unit tests for the ``exlab_wizard.api.errors`` envelope helpers."""

from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from starlette.requests import Request

from exlab_wizard.api.errors import (
    ERROR_CODES,
    EW_TRACE_ID_HEADER,
    build_error_envelope,
    error_response,
    extract_or_create_trace_id,
    register_exception_handlers,
)
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


def _bare_request(path: str = "/") -> Request:
    """Build a minimal Starlette Request for handler tests."""
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": [],
        "query_string": b"",
    }
    return Request(scope)


def test_build_envelope_includes_required_fields() -> None:
    envelope = build_error_envelope(
        code="validation_failed",
        message="invalid field",
        field="operator",
        details={"min_length": 1},
        trace_id="abc123",
    )
    assert envelope == {
        "error": {
            "code": "validation_failed",
            "message": "invalid field",
            "field": "operator",
            "details": {"min_length": 1},
            "trace_id": "abc123",
        }
    }


def test_build_envelope_drops_optional_fields_when_absent() -> None:
    envelope = build_error_envelope(code="internal_error", message="boom")
    assert "field" not in envelope["error"]
    assert "details" not in envelope["error"]


def test_unknown_code_falls_back_to_internal_error() -> None:
    envelope = build_error_envelope(code="not_a_real_code", message="x")
    assert envelope["error"]["code"] == "internal_error"


def test_extract_trace_id_uses_header_when_present() -> None:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(EW_TRACE_ID_HEADER.lower().encode(), b"deadbeef")],
        "query_string": b"",
    }
    request = Request(scope)
    assert extract_or_create_trace_id(request) == "deadbeef"


def test_extract_trace_id_generates_when_missing() -> None:
    request = _bare_request()
    trace_id = extract_or_create_trace_id(request)
    assert trace_id and isinstance(trace_id, str)


def test_error_response_sets_trace_id_header() -> None:
    response = error_response(
        request=None,
        code="validation_failed",
        message="x",
        status_code=422,
    )
    assert response.headers[EW_TRACE_ID_HEADER]
    body = json.loads(bytes(response.body))
    assert body["error"]["code"] == "validation_failed"


def test_required_codes_are_registered() -> None:
    """Every code in §4.6.3's table must be in :data:`ERROR_CODES`."""
    required = {
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
    assert required.issubset(ERROR_CODES)


def test_register_exception_handlers_catches_exlab_validation() -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/raise")
    async def raise_it() -> None:
        raise ExLabValidationError(
            {"code": "validation_failed", "message": "the message", "field": "label"}
        )

    client = TestClient(app)
    response = client.get("/raise")
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_failed"
    assert body["error"]["field"] == "label"


class _PydanticBody(BaseModel):
    """Module-level body class so FastAPI treats ``body: _PydanticBody``
    as a JSON body rather than a query parameter."""

    label: str


def test_register_exception_handlers_catches_pydantic_validation() -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.post("/p")
    async def post_p(body: _PydanticBody) -> dict:
        return {"ok": True}

    client = TestClient(app)
    response = client.post("/p", json={})
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_failed"
    assert body["error"]["field"] == "label"


def test_register_exception_handlers_catches_keyring_error() -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/k")
    async def k() -> None:
        raise KeyringUnavailableError("no backend")

    client = TestClient(app)
    response = client.get("/k")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "keyring_unavailable"


def test_register_exception_handlers_catches_schema_major_mismatch() -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/s")
    async def s() -> None:
        raise SchemaMajorMismatchError(expected_major=1, found="2.0")

    client = TestClient(app)
    response = client.get("/s")
    body = response.json()
    assert body["error"]["code"] == "schema_major_mismatch"
    assert body["error"]["details"]["found"] == "2.0"


def test_register_exception_handlers_catches_setup_incomplete() -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/set")
    async def s() -> None:
        raise SetupIncompleteError(
            {"state": "incomplete_no_lims", "missing": [{"field": "lims.endpoint"}]}
        )

    client = TestClient(app)
    response = client.get("/set")
    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "setup_incomplete"
    assert body["error"]["state"] == "incomplete_no_lims"


def test_register_exception_handlers_catches_config_error() -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/c")
    async def c() -> None:
        raise ConfigError("bad config")

    client = TestClient(app)
    response = client.get("/c")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


def test_register_exception_handlers_catches_template_load_error() -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/t")
    async def t() -> None:
        raise TemplateLoadError("template missing")

    client = TestClient(app)
    response = client.get("/t")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "template_load_error"


def test_register_exception_handlers_catches_template_core_field_redeclared() -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/tc")
    async def tc() -> None:
        raise TemplateCoreFieldRedeclaredError("redeclared label")

    client = TestClient(app)
    response = client.get("/tc")
    body = response.json()
    assert body["error"]["code"] == "template_core_field_redeclared"


def test_generic_handler_returns_internal_error() -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/g")
    async def g() -> None:
        raise RuntimeError("oops")

    # FastAPI's TestClient does not propagate uncaught exceptions in
    # the latest behavior; explicitly disable raising so the handler
    # fires.
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/g")
    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "internal_error"
    assert body["error"]["message"] == "internal server error"
