"""Tests for the logger factory in ``exlab_wizard.logging.manager``.

The factory is the only place in the codebase permitted to call
``logging.getLogger`` (Backend Spec §16.2.1). These tests pin the import paths,
the return type, and stdlib idempotency, so any future replacement of the
factory keeps that contract.
"""

from __future__ import annotations

import logging

import exlab_wizard.logging as logging_pkg


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
