"""Canonical logger factory + configuration. Backend Spec §16.2.1, §16.2.2, §16.2.5.

This module is the single allowed call site for ``logging.getLogger`` in
the codebase (§16.2.1; pre-commit lint enforces this). All component
authors import :func:`get_logger` from ``exlab_wizard.logging``.

The on-disk handler chain is wired up by :func:`configure_logging`, which
is invoked once during the FastAPI lifespan startup (§4.5) and may be
re-invoked after a ``PUT /api/v1/config`` to pick up a new
``logging.level`` or rotation policy.

The architecture follows §16.2.5: every ``logger.info(...)`` call enqueues
the record on a :class:`queue.Queue` via :class:`logging.handlers.QueueHandler`,
and a dedicated background thread (``QueueListener``) drains the queue
into the actual handlers (per-equipment file, central rotating file, and
the stderr stream). This keeps the asyncio event loop unblocked on log
calls while preserving stdlib compatibility for any third-party library
that does ``logging.getLogger(__name__).info(...)``.
"""

from __future__ import annotations

import contextlib
import logging
import logging.handlers
import queue
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from exlab_wizard.logging.format import StructuredTagFormatter
from exlab_wizard.logging.handlers import EquipmentScopedFileHandler
from exlab_wizard.paths import ensure_central_log_dir, os_central_log_path

if TYPE_CHECKING:
    from exlab_wizard.config.models import LoggingConfig

__all__ = [
    "_shutdown_logging",
    "configure_logging",
    "get_logger",
]


# ---------------------------------------------------------------------------
# Internal global state
# ---------------------------------------------------------------------------
#
# The manager keeps a single live ``QueueListener`` and the ``QueueHandler``
# attached to the root logger. Calling ``configure_logging`` again tears
# the listener down and rebuilds it; the queue handler on the root logger
# is replaced with one bound to the new queue.
#
# A module-level lock serializes ``configure_logging`` and
# ``_shutdown_logging`` so the launcher's startup/quit paths can't race.

_state_lock = threading.Lock()
_listener: logging.handlers.QueueListener | None = None
_queue: queue.Queue[logging.LogRecord] | None = None
_queue_handler: logging.handlers.QueueHandler | None = None
_threshold: int = logging.INFO


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_logger(name: str) -> logging.Logger:
    """Return a logger for ``name``.

    The ONLY place in the codebase that may call :func:`logging.getLogger`.
    Component authors must use this entry point; a pre-commit lint rule
    rejects direct ``logging.getLogger`` calls in any module under
    ``exlab_wizard/`` other than this one (§16.2.1).

    The returned logger inherits the root level set by
    :func:`configure_logging`. If ``configure_logging`` has not been
    called yet (e.g. during early module import or in unit tests that
    don't exercise the handler chain), the logger still works -- it just
    falls through to the stdlib root logger's defaults until the first
    ``configure_logging`` call.
    """
    return logging.getLogger(name)


def configure_logging(config: LoggingConfig | None = None) -> None:
    """Install the §16.2.5 queue-based handler chain.

    Idempotent: calling this a second time tears down the existing
    listener and rebuilds it with the new ``config``. In-flight log
    records that have already been enqueued are drained into the old
    handlers before they're replaced (see :class:`QueueListener.stop`),
    so a ``PUT /api/v1/config`` reconfigure does not lose log output.

    On first call:

    1. Sets the root logger's level threshold from ``config.level``
       (default ``INFO`` if ``config`` is ``None``).
    2. Creates a fresh unbounded :class:`queue.Queue`.
    3. Wires a :class:`QueueHandler` onto the root logger so every
       ``logger.info(...)`` returns immediately.
    4. Builds the real handlers (per-equipment file, central rotating
       file, stderr stream) and starts a :class:`QueueListener` thread
       that drains the queue into them.

    The per-equipment file handler is only installed when a
    ``local_root`` is configured (Phase 3A's ``configure_logging`` accepts
    a ``LoggingConfig`` only; future phases may pass an explicit
    ``local_root`` argument). When ``local_root`` is unset, the chain
    falls back to central + stderr only -- this keeps unit tests that
    don't model an equipment root from crashing.
    """
    with _state_lock:
        _teardown_locked()

        threshold = _resolve_threshold(config)
        max_mb, keep = _resolve_rotation(config)

        log_queue: queue.Queue[logging.LogRecord] = queue.Queue()
        queue_handler = logging.handlers.QueueHandler(log_queue)

        formatter = StructuredTagFormatter()
        real_handlers = _build_real_handlers(
            local_root=_resolve_local_root(config),
            central_max_bytes=max_mb * 1024 * 1024,
            central_backup_count=keep,
            formatter=formatter,
        )

        listener = logging.handlers.QueueListener(
            log_queue,
            *real_handlers,
            respect_handler_level=True,
        )
        listener.start()

        root = logging.getLogger()
        root.setLevel(threshold)
        # Strip any existing QueueHandler we previously installed so that
        # re-entry doesn't accumulate handlers across config reloads.
        for existing in list(root.handlers):
            if isinstance(existing, logging.handlers.QueueHandler):
                root.removeHandler(existing)
        root.addHandler(queue_handler)

        global _listener, _queue, _queue_handler, _threshold
        _listener = listener
        _queue = log_queue
        _queue_handler = queue_handler
        _threshold = threshold


def _shutdown_logging() -> None:
    """Drain the queue, stop the listener thread, and detach the queue handler.

    Called by the launcher's ``quit_coordinator`` (§4.3.2 / Phase 13).
    Idempotent: a second call is a no-op.
    """
    with _state_lock:
        _teardown_locked()


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _teardown_locked() -> None:
    """Tear down the active listener. Must be called with ``_state_lock`` held."""
    global _listener, _queue, _queue_handler
    if _listener is not None:
        # ``stop`` blocks until the listener drains the queue and joins
        # the worker thread, which is the §16.2.5 graceful shutdown
        # contract.
        _listener.stop()
        for handler in _listener.handlers:
            with contextlib.suppress(Exception):
                handler.close()
        _listener = None
    if _queue_handler is not None:
        root = logging.getLogger()
        root.removeHandler(_queue_handler)
        _queue_handler = None
    _queue = None


def _resolve_threshold(config: LoggingConfig | None) -> int:
    """Return the numeric level threshold for the root logger.

    ``LoggingConfig`` already validates ``level`` against the canonical set
    of stdlib level names (DEBUG / INFO / WARN / WARNING / ERROR), so the
    ``getLevelName`` lookup either returns the numeric level or ``INFO`` as
    a defensive fallback (a non-string return value indicates the validator
    let through an unknown name, which would be a configuration bug).
    """
    if config is None:
        return logging.INFO
    resolved = logging.getLevelName(config.level.upper())
    return resolved if isinstance(resolved, int) else logging.INFO


def _resolve_rotation(config: LoggingConfig | None) -> tuple[int, int]:
    """Return ``(central_log_max_mb, central_log_keep)`` from ``config`` or defaults."""
    if config is None:
        # Mirror LoggingConfig defaults (§9 / config models). Kept in sync
        # by the test suite -- a divergent default would surface as a
        # rotation-policy mismatch in a fixture.
        return (10, 5)
    return (config.central_log_max_mb, config.central_log_keep)


def _resolve_local_root(config: LoggingConfig | None) -> Path | None:
    """Return the configured ``local_root`` for the equipment-scoped handler.

    Phase 3A's ``LoggingConfig`` does NOT carry ``local_root`` -- that
    field lives on ``PathsConfig``. The launcher's bootstrap will pass an
    explicit ``Config`` snapshot in a later phase; for now this returns
    ``None`` and the equipment-scoped handler is skipped.
    """
    return None


def _build_real_handlers(
    *,
    local_root: Path | None,
    central_max_bytes: int,
    central_backup_count: int,
    formatter: logging.Formatter,
) -> list[logging.Handler]:
    """Build the listener's downstream handler chain.

    Order is fixed: equipment-scoped (when configured), central rotating,
    stderr. The ``QueueListener`` fans each record out to all of them.
    """
    handlers: list[logging.Handler] = []

    if local_root is not None:
        equip_handler = EquipmentScopedFileHandler(local_root=local_root)
        equip_handler.setFormatter(formatter)
        handlers.append(equip_handler)

    central_path = os_central_log_path()
    ensure_central_log_dir()
    central_handler = logging.handlers.RotatingFileHandler(
        central_path,
        maxBytes=central_max_bytes,
        backupCount=central_backup_count,
        encoding="utf-8",
    )
    central_handler.setFormatter(formatter)
    handlers.append(central_handler)

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.WARNING)
    stderr_handler.setFormatter(formatter)
    handlers.append(stderr_handler)

    return handlers
