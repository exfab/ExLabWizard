"""Tests for ``exlab_wizard.plugins.logger`` -- structured plugin log shim.

Pin both implementations:

- :class:`HostPluginLogger` forwards into the canonical
  :func:`exlab_wizard.logging.get_logger` chain at the right level.
- :class:`WorkerPluginLogger` emits one JSON-encoded
  :class:`PluginLogFrame` per call to its configured stream
  (defaults to ``sys.stderr``).

Backend Spec §6.1.4, §6.3.2.
"""

from __future__ import annotations

import io
import json
import logging

import pytest

from exlab_wizard.plugins.logger import (
    HostPluginLogger,
    PluginLogFrame,
    WorkerPluginLogger,
)

# ---------------------------------------------------------------------------
# HostPluginLogger -- forwards to the canonical stdlib logger.
# ---------------------------------------------------------------------------


def test_host_plugin_logger_forwards_info_to_stdlib_logger(caplog: pytest.LogCaptureFixture) -> None:
    log = HostPluginLogger(name="exlab_wizard.test.plugin.host_info")
    with caplog.at_level(logging.INFO, logger="exlab_wizard.test.plugin.host_info"):
        log.info("hello world")
    assert any(rec.message == "hello world" and rec.levelno == logging.INFO for rec in caplog.records)


def test_host_plugin_logger_forwards_debug(caplog: pytest.LogCaptureFixture) -> None:
    log = HostPluginLogger(name="exlab_wizard.test.plugin.host_debug")
    with caplog.at_level(logging.DEBUG, logger="exlab_wizard.test.plugin.host_debug"):
        log.debug("debug msg")
    assert any(rec.levelno == logging.DEBUG for rec in caplog.records)


def test_host_plugin_logger_forwards_warning(caplog: pytest.LogCaptureFixture) -> None:
    log = HostPluginLogger(name="exlab_wizard.test.plugin.host_warn")
    with caplog.at_level(logging.WARNING, logger="exlab_wizard.test.plugin.host_warn"):
        log.warning("warn msg")
    assert any(rec.levelno == logging.WARNING for rec in caplog.records)


def test_host_plugin_logger_forwards_error(caplog: pytest.LogCaptureFixture) -> None:
    log = HostPluginLogger(name="exlab_wizard.test.plugin.host_err")
    with caplog.at_level(logging.ERROR, logger="exlab_wizard.test.plugin.host_err"):
        log.error("err msg")
    assert any(rec.levelno == logging.ERROR for rec in caplog.records)


def test_host_plugin_logger_passes_structured_fields_via_extra(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Structured ``**fields`` arrive on the LogRecord as ``record.context``."""
    log = HostPluginLogger(name="exlab_wizard.test.plugin.host_fields")
    with caplog.at_level(logging.INFO, logger="exlab_wizard.test.plugin.host_fields"):
        log.info("event", file="x.txt", count=3)
    matched = [r for r in caplog.records if r.message == "event"]
    assert len(matched) == 1
    assert getattr(matched[0], "context", None) == {"file": "x.txt", "count": 3}


def test_host_plugin_logger_uses_default_logger_name_when_unspecified(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Construction without ``name`` falls back to ``exlab_wizard.plugins``."""
    log = HostPluginLogger()
    with caplog.at_level(logging.INFO, logger="exlab_wizard.plugins"):
        log.info("default-channel")
    assert any(r.name == "exlab_wizard.plugins" and r.message == "default-channel" for r in caplog.records)


# ---------------------------------------------------------------------------
# WorkerPluginLogger -- emits JSON frames to its stream.
# ---------------------------------------------------------------------------


def test_worker_plugin_logger_emits_json_frame_to_stream() -> None:
    stream = io.StringIO()
    log = WorkerPluginLogger(stream=stream)
    log.info("hello", file="x.txt")
    output = stream.getvalue()
    assert output.endswith("\n")
    payload = json.loads(output)
    assert payload["level"] == "INFO"
    assert payload["message"] == "hello"
    assert payload["context"] == {"file": "x.txt"}


def test_worker_plugin_logger_each_level_emits_correct_level_string() -> None:
    stream = io.StringIO()
    log = WorkerPluginLogger(stream=stream)
    log.debug("d")
    log.info("i")
    log.warning("w")
    log.error("e")
    lines = [line for line in stream.getvalue().splitlines() if line]
    assert len(lines) == 4
    levels = [json.loads(line)["level"] for line in lines]
    assert levels == ["DEBUG", "INFO", "WARNING", "ERROR"]


def test_worker_plugin_logger_emits_one_frame_per_call() -> None:
    stream = io.StringIO()
    log = WorkerPluginLogger(stream=stream)
    log.info("a")
    log.info("b")
    log.info("c")
    lines = [line for line in stream.getvalue().splitlines() if line]
    assert len(lines) == 3


def test_worker_plugin_logger_emits_empty_context_when_no_fields() -> None:
    stream = io.StringIO()
    log = WorkerPluginLogger(stream=stream)
    log.info("hello")
    payload = json.loads(stream.getvalue())
    assert payload["context"] == {}


def test_worker_plugin_logger_default_stream_is_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without an explicit stream, the worker writes to ``sys.stderr``."""
    fake_stderr = io.StringIO()
    monkeypatch.setattr("sys.stderr", fake_stderr)
    log = WorkerPluginLogger()
    log.info("over-stderr")
    assert fake_stderr.getvalue().strip() != ""
    payload = json.loads(fake_stderr.getvalue())
    assert payload["message"] == "over-stderr"


# ---------------------------------------------------------------------------
# PluginLogFrame Struct shape
# ---------------------------------------------------------------------------


def test_plugin_log_frame_fields_are_pinned() -> None:
    frame = PluginLogFrame(level="INFO", message="m", context={"a": 1})
    assert frame.level == "INFO"
    assert frame.message == "m"
    assert frame.context == {"a": 1}


def test_plugin_log_frame_default_context_is_empty_dict() -> None:
    frame = PluginLogFrame(level="INFO", message="m")
    assert frame.context == {}
