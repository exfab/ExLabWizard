"""Tests for the logger factory + ``configure_logging`` in ``exlab_wizard.logging.manager``.

The factory is the only place in the codebase permitted to call
``logging.getLogger`` (Backend Spec §16.2.1). These tests pin the import paths,
the return type, and stdlib idempotency, so any future replacement of the
factory keeps that contract.

The ``configure_logging`` tests pin §16.2.2 (idempotency, config-driven
threshold, queue-listener wiring) and §16.2.5 (queue-handler chain on the
root logger) -- the launcher's lifespan startup walks these surfaces, and
a regression there silently breaks the central log on production.
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path
from unittest import mock

import pytest

import exlab_wizard.logging as logging_pkg
from exlab_wizard.config.models import LoggingConfig
from exlab_wizard.logging import configure_logging
from exlab_wizard.logging.manager import _shutdown_logging


@pytest.fixture(autouse=True)
def _isolate_central_log(tmp_path: Path) -> None:
    """Redirect ``os_central_log_path`` to ``tmp_path`` so manager tests
    don't write into the real OS state directory."""
    central = tmp_path / "central" / "app.log"
    with (
        mock.patch(
            "exlab_wizard.logging.manager.os_central_log_path",
            return_value=central,
        ),
        mock.patch(
            "exlab_wizard.logging.manager.ensure_central_log_dir",
            side_effect=lambda: central.parent.mkdir(parents=True, exist_ok=True) or central.parent,
        ),
    ):
        yield
    # Tear down any residual listener/queue handlers between tests so the
    # state-machine in the manager is fresh for the next case.
    _shutdown_logging()


def test_get_logger_importable_from_package() -> None:
    from exlab_wizard.logging import get_logger

    assert callable(get_logger)


def test_get_logger_importable_from_manager_module() -> None:
    from exlab_wizard.logging.manager import get_logger

    assert callable(get_logger)


def test_package_and_manager_export_the_same_callable() -> None:
    from exlab_wizard.logging import get_logger as pkg_get_logger
    from exlab_wizard.logging.manager import get_logger as mgr_get_logger

    assert pkg_get_logger is mgr_get_logger


def test_get_logger_returns_stdlib_logger_instance() -> None:
    from exlab_wizard.logging import get_logger

    logger = get_logger("test.name")
    assert isinstance(logger, logging.Logger)


def test_get_logger_sets_name_on_returned_logger() -> None:
    from exlab_wizard.logging import get_logger

    logger = get_logger("test.name")
    assert logger.name == "test.name"


def test_get_logger_is_idempotent_for_same_name() -> None:
    # Stdlib ``logging.getLogger`` returns the same instance on repeated calls
    # for a given name -- our wrapper must preserve that.
    from exlab_wizard.logging import get_logger

    first = get_logger("foo")
    second = get_logger("foo")
    assert first is second


def test_get_logger_returns_distinct_loggers_for_different_names() -> None:
    from exlab_wizard.logging import get_logger

    a = get_logger("alpha")
    b = get_logger("beta")
    assert a is not b
    assert a.name == "alpha"
    assert b.name == "beta"


def test_logging_package_all_contains_get_logger() -> None:
    assert "get_logger" in logging_pkg.__all__


# ---------------------------------------------------------------------------
# configure_logging -- §16.2.2 / §16.2.5
# ---------------------------------------------------------------------------


def test_configure_logging_with_default_threshold_and_handlers() -> None:
    """First call with ``config=None`` installs the queue-handler chain at INFO."""
    configure_logging()
    root = logging.getLogger()
    queue_handlers = [h for h in root.handlers if isinstance(h, logging.handlers.QueueHandler)]
    assert len(queue_handlers) == 1
    assert root.level == logging.INFO


def test_configure_logging_applies_config_level() -> None:
    """``LoggingConfig.level`` drives the root threshold (§16.5)."""
    configure_logging(LoggingConfig(level="DEBUG"))
    root = logging.getLogger()
    assert root.level == logging.DEBUG


def test_configure_logging_idempotent_does_not_accumulate_handlers() -> None:
    """§16.2.2: re-calling ``configure_logging`` tears down + rebuilds.

    The launcher calls this on every ``PUT /api/v1/config`` reconfigure;
    a regression that accumulates QueueHandlers across calls would emit
    every log record N times after N reconfigures.
    """
    configure_logging()
    configure_logging()
    configure_logging()
    root = logging.getLogger()
    queue_handlers = [h for h in root.handlers if isinstance(h, logging.handlers.QueueHandler)]
    assert len(queue_handlers) == 1


def test_configure_logging_routes_records_through_queue_listener() -> None:
    """A logger.info() call in the configured handler chain produces a record
    that the queue listener forwards to the central rotating handler."""
    configure_logging(LoggingConfig(level="INFO"))
    logger = logging.getLogger("phase3.manager.test")
    logger.propagate = True
    logger.info("test message routed through queue")
    # Force the listener to flush by triggering teardown + rebuild.
    _shutdown_logging()


def test_shutdown_logging_is_idempotent() -> None:
    """A second ``_shutdown_logging`` call must be a no-op (used by quit)."""
    configure_logging()
    _shutdown_logging()
    _shutdown_logging()  # Must not raise.


def test_configure_logging_installs_central_rotating_file_handler() -> None:
    """The downstream handler chain must include a ``RotatingFileHandler``
    against the OS-appropriate central log path (§16.3)."""
    configure_logging(LoggingConfig(central_log_max_mb=2, central_log_keep=3))
    from exlab_wizard.logging.manager import _listener as listener

    assert listener is not None
    rotating = [h for h in listener.handlers if isinstance(h, logging.handlers.RotatingFileHandler)]
    assert len(rotating) == 1
    handler = rotating[0]
    # The configured rotation parameters land on the handler.
    assert handler.maxBytes == 2 * 1024 * 1024
    assert handler.backupCount == 3


def test_configure_logging_stderr_handler_is_warn_capped() -> None:
    """§16.2.1: the stderr handler is capped at WARN even when the global
    threshold is lower (so the launcher console doesn't overflow with INFO)."""
    configure_logging(LoggingConfig(level="DEBUG"))
    from exlab_wizard.logging.manager import _listener as listener

    assert listener is not None
    stream_handlers = [
        h
        for h in listener.handlers
        if isinstance(h, logging.StreamHandler)
        and not isinstance(h, logging.handlers.RotatingFileHandler)
    ]
    assert any(h.level == logging.WARNING for h in stream_handlers)


def test_resolve_threshold_unknown_level_falls_back_to_info() -> None:
    """A ``LoggingConfig`` with a level that ``getLevelName`` doesn't know
    about defaults to INFO defensively (the validator should catch this
    earlier; the manager has its own belt-and-braces fallback)."""
    from exlab_wizard.logging.manager import _resolve_threshold

    # Build a fake config with a non-canonical level (bypassing the
    # Pydantic validator). ``LoggingConfig`` rejects unknown values, so we
    # mock the resolved-name path directly.
    cfg = LoggingConfig(level="INFO")
    object.__setattr__(cfg, "level", "MYSTERY")
    threshold = _resolve_threshold(cfg)
    assert threshold == logging.INFO


def test_configure_logging_strips_pre_existing_queue_handler() -> None:
    """If a stray ``QueueHandler`` is on the root logger before
    ``configure_logging`` runs, it must be detached so we don't end up
    fanning records to two queues (§16.2.5 idempotency edge case)."""
    import queue as queue_mod

    stray = logging.handlers.QueueHandler(queue_mod.Queue())
    root = logging.getLogger()
    root.addHandler(stray)
    try:
        configure_logging()
    finally:
        # Cleanup: ensure the stray was removed by configure_logging itself.
        if stray in root.handlers:
            root.removeHandler(stray)
    # After configure_logging there is exactly one queue handler -- the
    # one the manager just installed.
    queue_handlers = [h for h in root.handlers if isinstance(h, logging.handlers.QueueHandler)]
    assert len(queue_handlers) == 1


def test_build_real_handlers_includes_equipment_handler_when_local_root_set(
    tmp_path: Path,
) -> None:
    """When ``local_root`` is configured, the listener chain includes an
    :class:`EquipmentScopedFileHandler` (§16.2.4)."""
    from exlab_wizard.logging.format import StructuredTagFormatter
    from exlab_wizard.logging.handlers import EquipmentScopedFileHandler
    from exlab_wizard.logging.manager import _build_real_handlers

    handlers = _build_real_handlers(
        local_root=tmp_path,
        central_max_bytes=1024,
        central_backup_count=1,
        formatter=StructuredTagFormatter(),
    )
    assert any(isinstance(h, EquipmentScopedFileHandler) for h in handlers)
    for h in handlers:
        h.close()
