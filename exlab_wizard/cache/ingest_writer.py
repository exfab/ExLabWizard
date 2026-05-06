"""Orchestrator-only writer for ``ingest.json``. Backend Spec §13.4, §4.4.5.

The orchestrator writes one ``ingest.json`` per staged run, capturing the
five-state lifecycle (§13.3): ``staging`` -> ``complete`` -> ``sync_queued``
-> ``sync_verified`` -> ``cleared``. State transitions are append-only -- a
new entry is added to the ``history`` array on every transition, and the
top-level ``current_state`` mirrors the latest entry.

Disk-side guarantees per §4.4.5:

* ``msgspec.json`` for typed encode/decode (schema validation in one pass).
* ``filelock.FileLock`` advisory exclusive lock around the read-mutate-write
  cycle so concurrent appends never lose entries.
* Atomic write via tempfile + ``fsync`` + ``os.replace``.

Reads enforce the §11.9.2 reader policy: a file at a different schema major
than the writer raises ``SchemaMajorMismatchError`` (no silent partial-parse
across major boundaries).
"""

from __future__ import annotations

import asyncio
import os
import socket
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import msgspec
from filelock import FileLock

from exlab_wizard.api.schemas import IngestJson
from exlab_wizard.constants import INGEST_JSON_VERSION, IngestState
from exlab_wizard.errors import SchemaMajorMismatchError
from exlab_wizard.logging import get_logger

__all__ = ["IngestWriter"]

_logger = get_logger(__name__)

# Forward state-machine map per §13.3. Each key is the source state; the
# value is the set of states reachable in one step. Any transition not
# present here is rejected by ``append_state_transition``.
_FORWARD_TRANSITIONS: dict[IngestState, frozenset[IngestState]] = {
    IngestState.STAGING: frozenset({IngestState.COMPLETE}),
    IngestState.COMPLETE: frozenset({IngestState.SYNC_QUEUED}),
    IngestState.SYNC_QUEUED: frozenset({IngestState.SYNC_VERIFIED}),
    IngestState.SYNC_VERIFIED: frozenset({IngestState.CLEARED}),
    IngestState.CLEARED: frozenset(),
}


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 with the trailing ``Z`` per §13.4."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _expected_major() -> int:
    """Return the major component of ``INGEST_JSON_VERSION`` as an int."""
    return int(INGEST_JSON_VERSION.split(".", 1)[0])


def _check_schema_major(found: str) -> None:
    """Raise ``SchemaMajorMismatchError`` when ``found`` is a different major.

    Backend Spec §11.9.2 rule 3: refuse any file at version ``M.x`` where
    ``M != R.major``. Same major (older or newer minor) is allowed.
    """
    expected = _expected_major()
    try:
        found_major = int(str(found).split(".", 1)[0])
    except (ValueError, AttributeError) as exc:
        # Malformed schema_version is treated as a major mismatch -- the
        # reader cannot tell what version the file claims to be.
        raise SchemaMajorMismatchError(expected_major=expected, found=str(found)) from exc
    if found_major != expected:
        raise SchemaMajorMismatchError(expected_major=expected, found=str(found))


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically.

    Per §4.4.5: tmp file + ``fsync`` + ``os.replace``. The ``.tmp`` sibling
    sits next to the target so the rename is on the same volume (atomic on
    POSIX, atomic-on-same-volume on Windows).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)


class IngestWriter:
    """Writer for ``ingest.json``. Orchestrator-mode only.

    Backend Spec §13.4. State history is append-only (§13.3) to preserve full
    audit history -- the writer never edits or deletes prior entries.

    All public methods are ``async`` to match the §4.4.5 ``CacheWriter``
    contract; the blocking lock + I/O work is dispatched through
    ``asyncio.to_thread`` so the FastAPI event loop is never blocked.
    """

    async def write_ingest(self, path: Path, payload: IngestJson) -> None:
        """Write ``payload`` to ``path`` atomically under an exclusive lock.

        Reserved for the initial-creation path (no file exists yet); the
        per-file lock is taken defensively so a concurrent initial-write
        attempt serializes rather than races.
        """
        await asyncio.to_thread(self._write_ingest_blocking, path, payload)

    async def read_ingest(self, path: Path) -> IngestJson:
        """Read and decode ``path`` into an ``IngestJson``.

        Raises ``SchemaMajorMismatchError`` (§11.9.2) when the on-disk file
        carries a different schema major than ``INGEST_JSON_VERSION``.
        """
        return await asyncio.to_thread(self._read_ingest_blocking, path)

    async def append_state_transition(
        self,
        path: Path,
        new_state: IngestState,
        *,
        host: str,
        files_received: int | None = None,
        bytes_received: int | None = None,
        nas_path: str | None = None,
        checksum_file: str | None = None,
    ) -> IngestJson:
        """Append a state-transition entry and update ``current_state``.

        File-locked for the entire read-mutate-write cycle so concurrent
        callers never lose entries. The new history entry has the shape::

            {"state": "<new_state>", "at": "<UTC ISO 8601>", "host": "<host>"}

        Per §13.4 the entry carries optional extras when transitioning to
        specific states:

        * ``complete`` -- ``files_received`` and ``bytes_received``.
        * ``sync_verified`` -- ``nas_path`` and ``checksum_file``.

        Other state transitions ignore those extras (they are silently dropped
        because the spec does not define their meaning at those states).

        Raises ``ValueError`` if ``new_state`` is not a permitted forward
        transition from the file's current state. Going backward (e.g.
        ``cleared`` -> ``staging``) is rejected. The full state machine is
        documented in ``_FORWARD_TRANSITIONS`` above and §13.3.
        """
        return await asyncio.to_thread(
            self._append_state_transition_blocking,
            path,
            new_state,
            host,
            files_received,
            bytes_received,
            nas_path,
            checksum_file,
        )

    # ---- Blocking helpers (run via asyncio.to_thread) ---------------------

    def _write_ingest_blocking(self, path: Path, payload: IngestJson) -> None:
        with FileLock(str(path) + ".lock"):
            _atomic_write_bytes(path, msgspec.json.encode(payload))
        _logger.info(
            "ingest.json written: %s (current_state=%s)",
            path,
            payload.current_state,
        )

    def _read_ingest_blocking(self, path: Path) -> IngestJson:
        with FileLock(str(path) + ".lock"):
            raw = path.read_bytes()
        # First decode loosely to inspect ``schema_version``: a major
        # mismatch must surface a structured error rather than be swallowed
        # by msgspec's typed decode (which would either succeed silently on
        # a future-major file with a compatible shape or raise a generic
        # validation error). Per §11.9.2 we explicitly check the major.
        meta: dict[str, Any] = msgspec.json.decode(raw)
        _check_schema_major(meta.get("schema_version", ""))
        return msgspec.json.decode(raw, type=IngestJson)

    def _append_state_transition_blocking(
        self,
        path: Path,
        new_state: IngestState,
        host: str,
        files_received: int | None,
        bytes_received: int | None,
        nas_path: str | None,
        checksum_file: str | None,
    ) -> IngestJson:
        with FileLock(str(path) + ".lock"):
            raw = path.read_bytes()
            meta: dict[str, Any] = msgspec.json.decode(raw)
            _check_schema_major(meta.get("schema_version", ""))
            payload = msgspec.json.decode(raw, type=IngestJson)

            current = IngestState(payload.current_state)
            allowed = _FORWARD_TRANSITIONS[current]
            if new_state not in allowed:
                raise ValueError(
                    f"Invalid ingest state transition: {current.value} -> {new_state.value}. "
                    f"Allowed forward transitions from {current.value}: "
                    f"{sorted(s.value for s in allowed)}",
                )

            entry: dict[str, Any] = {
                "state": new_state.value,
                "at": _now_iso(),
                "host": host,
            }
            if new_state is IngestState.COMPLETE:
                if files_received is not None:
                    entry["files_received"] = files_received
                if bytes_received is not None:
                    entry["bytes_received"] = bytes_received
            elif new_state is IngestState.SYNC_VERIFIED:
                if nas_path is not None:
                    entry["nas_path"] = nas_path
                if checksum_file is not None:
                    entry["checksum_file"] = checksum_file

            new_history = [*payload.history, entry]
            new_payload = msgspec.structs.replace(
                payload,
                current_state=new_state.value,
                history=new_history,
            )
            _atomic_write_bytes(path, msgspec.json.encode(new_payload))

        _logger.info(
            "ingest.json transition: %s -> %s (host=%s, path=%s)",
            current.value,
            new_state.value,
            host,
            path,
        )
        return new_payload


# Convenience for callers that need a default host string. Not part of the
# public class -- exposed for tests and the orchestrator session bootstrap.
def default_host() -> str:
    """Return ``socket.gethostname()`` (orchestrator default for ``host``)."""
    return socket.gethostname()
