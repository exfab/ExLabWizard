"""Sync transports package. Backend Spec §7.1.3.

Each transport is a thin async wrapper around an upstream binary. They
share a small common shape (``TransportResult``, ``TransportErrorKind``)
so the queue worker can treat outcomes uniformly while logging the raw
stdout/stderr for diagnostics.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "TransportError",
    "TransportErrorKind",
    "TransportResult",
]


class TransportErrorKind(StrEnum):
    """Kind of failure that the queue worker uses to drive the retry policy.

    Backend Spec §7.1.5.

    - ``NETWORK``: timeout, ECONNRESET, transient SSH failure -- retried with
      exponential backoff up to ``MAX_ATTEMPTS``.
    - ``AUTH``: authentication failure -- terminal FAILED, no retry.
    - ``HASH_MISMATCH``: post-transport hash check failed -- single retry of
      the transport phase, then terminal.
    - ``LOCAL_FILE_VANISHED``: the local file disappeared between transport
      and verify -- terminal FAILED with ``local_file_vanished`` reason.
    - ``UNKNOWN``: catch-all for transports returning a non-zero code we
      don't recognize -- treated as ``NETWORK`` for retry purposes.
    """

    NETWORK = "network"
    AUTH = "auth"
    HASH_MISMATCH = "hash_mismatch"
    LOCAL_FILE_VANISHED = "local_file_vanished"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class TransportResult:
    """Outcome of a transport push.

    ``ok`` is True iff the transport reported success. On failure,
    ``error_kind`` selects the retry path; ``stderr`` is the raw stderr
    text for log surfacing; ``returncode`` is the subprocess exit code.
    """

    ok: bool
    error_kind: TransportErrorKind | None = None
    stderr: str = ""
    stdout: str = ""
    returncode: int = 0


class TransportError(Exception):
    """Raised when a transport's external dependency is unusable.

    Distinct from :class:`TransportResult`: this is for cases where the
    upstream binary is missing entirely (e.g. ``rclone`` not on PATH).
    """
