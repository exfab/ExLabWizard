"""Tests for ``exlab_wizard.logging.handlers``.

Backend Spec §16.2.4 commits the equipment-scoped file handler contract:
the destination is resolved at emit time from the active context, the
file descriptor is opened lazily and cached for the process lifetime,
``fsync`` is invoked only on ``ERROR``-level emits, and emits without an
``equipment_id`` are skipped (they fall through to the central handler).

The handler implementation is the only place in the codebase that opens
the per-equipment ``wizard.<hostname>.log`` files, so a regression here
breaks the audit trail.
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest import mock

import pytest

from exlab_wizard.constants import CACHE_DIR_NAME, LOG_FILE_TEMPLATE
from exlab_wizard.logging.context import clear_run_context, set_run_context
from exlab_wizard.logging.format import StructuredTagFormatter
from exlab_wizard.logging.handlers import EquipmentScopedFileHandler


@pytest.fixture(autouse=True)
def _reset_context() -> None:
    """Wipe the run-context vars between cases so handler tests are isolated."""
    clear_run_context()


def _make_handler(local_root: Path) -> EquipmentScopedFileHandler:
    """Construct a handler with a deterministic hostname for test paths."""
    handler = EquipmentScopedFileHandler(local_root=local_root, hostname="labpc-04")
    handler.setFormatter(StructuredTagFormatter())
    return handler


def _make_record(level: int = logging.INFO, message: str = "hello") -> logging.LogRecord:
    record = logging.LogRecord(
        name="test",
        level=level,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=None,
        exc_info=None,
    )
    record.created = 1776436320.0
    return record


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def test_writes_to_equipment_scoped_path(tmp_path: Path) -> None:
    handler = _make_handler(tmp_path)
    expected_path = (
        tmp_path / "CONFOCAL_01" / CACHE_DIR_NAME / LOG_FILE_TEMPLATE.format(hostname="labpc-04")
    )

    with set_run_context(equipment_id="CONFOCAL_01"):
        handler.emit(_make_record(message="creation started"))

    handler.close()

    assert expected_path.exists()
    contents = expected_path.read_text(encoding="utf-8")
    assert "creation started" in contents


def test_writes_to_separate_files_per_equipment(tmp_path: Path) -> None:
    handler = _make_handler(tmp_path)
    path_a = (
        tmp_path / "CONFOCAL_01" / CACHE_DIR_NAME / LOG_FILE_TEMPLATE.format(hostname="labpc-04")
    )
    path_b = (
        tmp_path / "CONFOCAL_02" / CACHE_DIR_NAME / LOG_FILE_TEMPLATE.format(hostname="labpc-04")
    )

    with set_run_context(equipment_id="CONFOCAL_01"):
        handler.emit(_make_record(message="alpha"))
    with set_run_context(equipment_id="CONFOCAL_02"):
        handler.emit(_make_record(message="beta"))

    handler.close()

    assert "alpha" in path_a.read_text(encoding="utf-8")
    assert "alpha" not in path_b.read_text(encoding="utf-8")
    assert "beta" in path_b.read_text(encoding="utf-8")
    assert "beta" not in path_a.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Caching of file descriptors
# ---------------------------------------------------------------------------


def test_subsequent_emits_reuse_the_same_file_descriptor(tmp_path: Path) -> None:
    handler = _make_handler(tmp_path)

    with set_run_context(equipment_id="CONFOCAL_01"):
        handler.emit(_make_record(message="first"))
        first_entry = handler._files["CONFOCAL_01"]
        first_fp = first_entry._fp

        handler.emit(_make_record(message="second"))
        handler.emit(_make_record(message="third"))

        # Same wrapper, same underlying file pointer -- no re-open.
        assert handler._files["CONFOCAL_01"] is first_entry
        assert handler._files["CONFOCAL_01"]._fp is first_fp

    handler.close()

    log_path = (
        tmp_path / "CONFOCAL_01" / CACHE_DIR_NAME / LOG_FILE_TEMPLATE.format(hostname="labpc-04")
    )
    contents = log_path.read_text(encoding="utf-8")
    assert "first" in contents
    assert "second" in contents
    assert "third" in contents


def test_one_open_per_equipment_even_with_many_emits(tmp_path: Path) -> None:
    handler = _make_handler(tmp_path)
    from exlab_wizard.logging import handlers as handlers_module

    original_open = handlers_module._OpenLogFile.open
    with mock.patch.object(
        handlers_module._OpenLogFile,
        "open",
        wraps=original_open,
    ) as spy:
        with set_run_context(equipment_id="CONFOCAL_01"):
            for i in range(5):
                handler.emit(_make_record(message=f"event_{i}"))
        assert spy.call_count == 1

    handler.close()


# ---------------------------------------------------------------------------
# Skipping when no equipment_id is set
# ---------------------------------------------------------------------------


def test_emit_without_equipment_id_writes_no_file(tmp_path: Path) -> None:
    handler = _make_handler(tmp_path)

    # No ``set_run_context`` -- equipment_id is None.
    handler.emit(_make_record(message="orphan"))

    handler.close()

    # No equipment subdirectory was created.
    assert list(tmp_path.iterdir()) == []


def test_emit_with_explicit_none_equipment_skipped(tmp_path: Path) -> None:
    handler = _make_handler(tmp_path)

    # An outer with-block that establishes context but leaves equipment_id
    # at None still skips the emit.
    with set_run_context(host="labpc-04"):
        handler.emit(_make_record(message="bare"))

    handler.close()
    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------------
# fsync only on ERROR
# ---------------------------------------------------------------------------


def test_fsync_invoked_only_on_error_level(tmp_path: Path) -> None:
    handler = _make_handler(tmp_path)

    with mock.patch("exlab_wizard.logging.handlers.os.fsync") as mock_fsync:
        with set_run_context(equipment_id="CONFOCAL_01"):
            handler.emit(_make_record(level=logging.DEBUG, message="d"))
            handler.emit(_make_record(level=logging.INFO, message="i"))
            handler.emit(_make_record(level=logging.WARNING, message="w"))
            handler.emit(_make_record(level=logging.ERROR, message="e"))
            handler.emit(_make_record(level=logging.CRITICAL, message="c"))

        # ERROR + CRITICAL trigger fsync (CRITICAL >= ERROR), DEBUG/INFO/WARN
        # do not.
        assert mock_fsync.call_count == 2

    handler.close()


def test_fsync_on_error_passes_correct_fd(tmp_path: Path) -> None:
    handler = _make_handler(tmp_path)

    with mock.patch("exlab_wizard.logging.handlers.os.fsync") as mock_fsync:
        with set_run_context(equipment_id="CONFOCAL_01"):
            handler.emit(_make_record(level=logging.ERROR, message="boom"))
            cached_fd = handler._files["CONFOCAL_01"]._fileno_cached

        mock_fsync.assert_called_once_with(cached_fd)

    handler.close()


# ---------------------------------------------------------------------------
# Close behavior
# ---------------------------------------------------------------------------


def test_close_drains_cached_files(tmp_path: Path) -> None:
    handler = _make_handler(tmp_path)

    with set_run_context(equipment_id="CONFOCAL_01"):
        handler.emit(_make_record(message="alpha"))
    with set_run_context(equipment_id="CONFOCAL_02"):
        handler.emit(_make_record(message="beta"))

    assert len(handler._files) == 2

    handler.close()

    assert handler._files == {}


def test_close_is_idempotent(tmp_path: Path) -> None:
    handler = _make_handler(tmp_path)
    with set_run_context(equipment_id="CONFOCAL_01"):
        handler.emit(_make_record(message="x"))
    handler.close()
    # A second close call must not raise (e.g. closing an already-closed
    # file).
    handler.close()


# ---------------------------------------------------------------------------
# Error-path handling on emit (stdlib ``handleError`` contract)
# ---------------------------------------------------------------------------


def test_emit_routes_unexpected_exception_through_handle_error(tmp_path: Path) -> None:
    """The ``Exception`` branch in :meth:`emit` is the stdlib handler-error
    contract: any error inside the emit body must funnel into
    :meth:`logging.Handler.handleError` rather than propagate, so a single
    bad record can't poison the whole listener thread (§16.2.5)."""
    handler = _make_handler(tmp_path)
    with (
        mock.patch.object(handler, "handleError") as mock_handle_error,
        mock.patch.object(handler, "_open_for", side_effect=OSError("disk full")),
        set_run_context(equipment_id="CONFOCAL_01"),
    ):
        handler.emit(_make_record(message="boom"))
    mock_handle_error.assert_called_once()
