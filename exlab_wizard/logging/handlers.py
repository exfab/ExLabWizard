"""Equipment-scoped file handler. Backend Spec §16.2.4.

The :class:`EquipmentScopedFileHandler` resolves its destination at
``emit`` time using the active context's ``equipment_id``. There is one
file descriptor open per equipment, cached for the lifetime of the
process; concurrent emits from the same hostname use ``O_APPEND``
semantics (POSIX) or ``FILE_APPEND_DATA | FILE_SHARE_WRITE`` (Windows) so
writes don't tear (§4.5 same-equipment concurrency rule).

``fsync`` is called only on ``ERROR``-level events. ``INFO``/``DEBUG``
emits are flushed but not fsync'd, matching §16.2.4: durability matters
for hard failures, throughput matters for routine traffic.

Events without an ``equipment_id`` in context are silently skipped here
-- they reach the central handler via the same queue listener.
"""

from __future__ import annotations

import contextlib
import logging
import os
import socket
import sys
import threading
from pathlib import Path
from typing import IO

from exlab_wizard.constants import CACHE_DIR_NAME, LOG_FILE_TEMPLATE
from exlab_wizard.logging.context import get_run_context

__all__ = [
    "EquipmentScopedFileHandler",
]


class EquipmentScopedFileHandler(logging.Handler):
    """Per-equipment ``wizard.<hostname>.log`` writer.

    Resolves ``<local_root>/<equipment_id>/.exlab-wizard/wizard.<hostname>.log``
    at emit time from the active ``equipment_id`` context var. Lazy-opens
    the file on first emit and caches the file object per equipment for
    the process lifetime. ``O_APPEND`` semantics on POSIX (and the
    equivalent share-mode flags on Windows) keep concurrent emits
    tear-free without explicit locking.

    Construct with the configured ``local_root`` (a :class:`pathlib.Path`).
    The handler accepts an optional ``hostname`` kwarg so tests can pin a
    deterministic value; production callers leave it as ``None`` and the
    handler resolves ``socket.gethostname()`` once on construction.

    Events without an ``equipment_id`` in context are skipped: they fall
    through to the central handler downstream (which has no scope
    requirement).
    """

    def __init__(
        self,
        local_root: Path,
        *,
        hostname: str | None = None,
    ) -> None:
        super().__init__()
        self._local_root = Path(local_root)
        self._hostname = hostname or socket.gethostname()
        # equipment_id -> file object. The lock guards both lookup-and-open
        # (to prevent two concurrent emits for the same equipment from
        # opening twice) and close-on-shutdown.
        self._files: dict[str, _OpenLogFile] = {}
        self._lock = threading.Lock()

    # -- logging.Handler overrides ------------------------------------------

    def emit(self, record: logging.LogRecord) -> None:
        """Write ``record`` to the active equipment's log file."""
        try:
            equipment_id = get_run_context().get("equipment_id")
            if not equipment_id:
                # No scope -- the central handler will pick this up.
                return
            entry = self._open_for(equipment_id)
            line = self.format(record) + "\n"
            entry.write(line)
            if record.levelno >= logging.ERROR:
                entry.fsync()
        except Exception:
            # stdlib handler error contract (see ``logging.Handler.emit``).
            self.handleError(record)

    def close(self) -> None:
        """Close every cached file descriptor.

        Called on listener teardown. Idempotent: a second call is a no-op
        because the cache is cleared on first close.
        """
        with self._lock:
            entries = list(self._files.values())
            self._files.clear()
        for entry in entries:
            entry.close()
        super().close()

    # -- internals ----------------------------------------------------------

    def _open_for(self, equipment_id: str) -> _OpenLogFile:
        """Return the cached :class:`_OpenLogFile` for ``equipment_id``.

        Opens the destination on first request. The path is composed at
        emit time so a config reload that changes ``local_root`` is picked
        up on the next emit (the old descriptor is left to GC if it's
        still in use; in practice the listener is torn down on
        ``configure_logging`` re-entry, which closes everything).
        """
        with self._lock:
            existing = self._files.get(equipment_id)
            if existing is not None:
                return existing
            path = self._compose_path(equipment_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            entry = _OpenLogFile.open(path)
            self._files[equipment_id] = entry
            return entry

    def _compose_path(self, equipment_id: str) -> Path:
        """Return ``<local_root>/<equipment_id>/.exlab-wizard/wizard.<host>.log``."""
        return (
            self._local_root
            / equipment_id
            / CACHE_DIR_NAME
            / LOG_FILE_TEMPLATE.format(hostname=self._hostname)
        )


# ---------------------------------------------------------------------------
# _OpenLogFile -- the per-equipment file wrapper
# ---------------------------------------------------------------------------


class _OpenLogFile:
    """Append-mode log file with platform-appropriate share / append flags.

    The wrapper is intentionally minimal -- it owns the ``open()`` call and
    the matching ``close()``, plus a ``write`` and ``fsync`` pair the
    handler uses on hot paths. We do NOT subclass ``io.TextIOBase`` to
    avoid pulling in the buffer-manager state machine; the handler holds
    the only reference and serializes calls through the queue listener
    thread.
    """

    __slots__ = ("_fileno_cached", "_fp", "_lock")

    def __init__(self, fp: IO[str]) -> None:
        self._fp = fp
        self._fileno_cached = fp.fileno()
        self._lock = threading.Lock()

    @classmethod
    def open(cls, path: Path) -> _OpenLogFile:
        """Open ``path`` in append mode with the platform-appropriate flags."""
        # POSIX: O_APPEND guarantees that each write() lands at end-of-file
        # atomically, even with multiple writers. Windows: opening the file
        # in append mode via the C runtime translates to FILE_APPEND_DATA;
        # the additional FILE_SHARE_WRITE flag is the default for Python's
        # ``open`` on Windows so we don't need to drop into the Win32 API.
        fp: IO[str]
        if sys.platform == "win32":
            # ``Path.open`` with mode "a" on Windows yields FILE_APPEND_DATA +
            # FILE_SHARE_READ + FILE_SHARE_WRITE under the hood, matching
            # the §16.2.4 requirement for tear-free concurrent emits from
            # the same hostname.
            fp = path.open("a", encoding="utf-8", buffering=1)
        else:
            # POSIX: mode "a" sets O_APPEND, which is what §16.2.4 calls
            # for. Buffering=1 means line-buffered text mode.
            fd = os.open(
                path,
                os.O_WRONLY | os.O_CREAT | os.O_APPEND,
                0o644,
            )
            fp = os.fdopen(fd, "a", encoding="utf-8", buffering=1)
        return cls(fp)

    def write(self, line: str) -> None:
        """Append ``line`` (already newline-terminated) to the file."""
        with self._lock:
            self._fp.write(line)
            self._fp.flush()

    def fsync(self) -> None:
        """``os.fsync`` the underlying file descriptor.

        Called only on ``ERROR``-level emits per §16.2.4.
        """
        with self._lock:
            os.fsync(self._fileno_cached)

    def close(self) -> None:
        """Flush and close the file. Idempotent."""
        with self._lock:
            with contextlib.suppress(Exception):
                self._fp.flush()
            self._fp.close()
