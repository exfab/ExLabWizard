"""Plugin logger shim. Backend Spec §6.1.4 / §6.3.2.

Plugins call ``ctx.log.info(...)`` / ``ctx.log.warning(...)`` rather than
``print()`` or ``logging.getLogger(...)`` directly because:

1. In the worker subprocess (the production path), ``stdout`` is reserved
   for the IPC envelope -- a stray ``print`` corrupts the protocol. Stderr
   carries structured log frames the host re-emits into the wizard log.
2. In-process invocations (unit tests, ``--no-isolation`` debug) need a
   logger that forwards into the canonical
   :func:`exlab_wizard.logging.get_logger` chain so log records show up
   alongside everything else.

The :class:`PluginLogger` base type is the abstract surface plugins see;
the two implementations -- :class:`HostPluginLogger` (in-process) and
:class:`WorkerPluginLogger` (subprocess) -- share the same four-method
shape (``debug`` / ``info`` / ``warning`` / ``error``).

Each call accepts a positional ``message`` and arbitrary structured
``**fields`` keyword args; the host logger renders them as ``key=value``
extras while the worker logger emits them inside the JSON frame's
``context`` block (which the host then merges back in). Plugin authors
do not need to know which path they're on -- the API is identical.
"""

from __future__ import annotations

import logging
import sys
from abc import ABC, abstractmethod
from typing import IO, Any

import msgspec
from msgspec import json as msgspec_json

from exlab_wizard.logging import get_logger

__all__ = [
    "HostPluginLogger",
    "PluginLogFrame",
    "PluginLogger",
    "WorkerPluginLogger",
]


class PluginLogFrame(msgspec.Struct, frozen=True):
    """Wire format for worker-side log records.

    The worker subprocess emits one ``PluginLogFrame``-shaped JSON object
    per log line on its stderr stream; the host parses each line and
    forwards into the canonical logger chain. Backend Spec §6.3.2.
    """

    level: str
    message: str
    context: dict[str, Any] = {}


class PluginLogger(ABC):
    """Abstract structured-log interface plugins receive on ``ctx.log``.

    Implementations forward to either the in-process stdlib logger
    (:class:`HostPluginLogger`) or the worker stderr channel
    (:class:`WorkerPluginLogger`). Both expose the same four-method shape;
    plugin authors should not reach behind it.
    """

    @abstractmethod
    def debug(self, message: str, **fields: Any) -> None:
        """Emit a DEBUG-level structured log record."""

    @abstractmethod
    def info(self, message: str, **fields: Any) -> None:
        """Emit an INFO-level structured log record."""

    @abstractmethod
    def warning(self, message: str, **fields: Any) -> None:
        """Emit a WARNING-level structured log record."""

    @abstractmethod
    def error(self, message: str, **fields: Any) -> None:
        """Emit an ERROR-level structured log record."""


class HostPluginLogger(PluginLogger):
    """In-process forwarder used when plugins run without subprocess isolation.

    Used by the host's unit-test shim, by ``--no-isolation`` debug
    invocations of the plugin CLI (Backend Spec §6.10), and by the host
    when re-emitting parsed worker frames. Routes through
    :func:`exlab_wizard.logging.get_logger` so records flow into the
    canonical handler chain (per-equipment file, central rotating, stderr).
    """

    def __init__(self, name: str = "exlab_wizard.plugins") -> None:
        self._logger: logging.Logger = get_logger(name)

    def debug(self, message: str, **fields: Any) -> None:
        self._emit(logging.DEBUG, message, fields)

    def info(self, message: str, **fields: Any) -> None:
        self._emit(logging.INFO, message, fields)

    def warning(self, message: str, **fields: Any) -> None:
        self._emit(logging.WARNING, message, fields)

    def error(self, message: str, **fields: Any) -> None:
        self._emit(logging.ERROR, message, fields)

    def _emit(self, level: int, message: str, fields: dict[str, Any]) -> None:
        # Pass the structured fields through ``extra`` so the canonical
        # formatter (StructuredTagFormatter) can render them; if none were
        # provided we still emit a plain record so call sites without
        # context still log.
        if fields:
            self._logger.log(level, message, extra={"context": fields})
        else:
            self._logger.log(level, message)


class WorkerPluginLogger(PluginLogger):
    """Worker-side forwarder. Emits JSON frames on stderr.

    Used inside the plugin worker subprocess (Backend Spec §6.3.1). Each
    call serializes a :class:`PluginLogFrame` via :mod:`msgspec.json` and
    writes a single newline-terminated line to the configured stream
    (defaults to ``sys.stderr``). The host reads these line-by-line and
    forwards them to the canonical logger.

    The worker MUST NOT use stdout for log output -- that channel is
    reserved for the IPC envelope. Stderr is the structured-log
    sideband.
    """

    _encoder: msgspec_json.Encoder = msgspec_json.Encoder()

    def __init__(self, stream: IO[str] | None = None) -> None:
        self._stream: IO[str] = stream if stream is not None else sys.stderr

    def debug(self, message: str, **fields: Any) -> None:
        self._emit("DEBUG", message, fields)

    def info(self, message: str, **fields: Any) -> None:
        self._emit("INFO", message, fields)

    def warning(self, message: str, **fields: Any) -> None:
        self._emit("WARNING", message, fields)

    def error(self, message: str, **fields: Any) -> None:
        self._emit("ERROR", message, fields)

    def _emit(self, level: str, message: str, fields: dict[str, Any]) -> None:
        frame = PluginLogFrame(level=level, message=message, context=fields)
        encoded = self._encoder.encode(frame).decode("utf-8")
        self._stream.write(encoded + "\n")
        self._stream.flush()
