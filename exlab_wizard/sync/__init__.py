"""Sync package. Backend Spec §7.

Public re-exports for the NAS sync subsystem. Callers should depend on
this package's surface rather than reach into the sub-modules; the
:class:`NASSyncClient` is the only stateful object outside callers
typically interact with.
"""

from exlab_wizard.constants import SyncHandleState
from exlab_wizard.sync.bandwidth import effective_bandwidth_limit_kibps
from exlab_wizard.sync.cleanup import cleanup_interlocks_satisfied
from exlab_wizard.sync.nas_client import (
    NASSyncClient,
    SyncJobHandle,
)
from exlab_wizard.sync.pre_sync_gate import is_eligible
from exlab_wizard.sync.queue import (
    BACKOFF_SCHEDULE_SECONDS,
    MAX_ATTEMPTS,
    SyncJobRow,
    SyncJobState,
    SyncQueue,
)
from exlab_wizard.sync.transports import (
    TransportError,
    TransportErrorKind,
    TransportResult,
)
from exlab_wizard.sync.verifier import Verifier, VerifyResult

# Backward-compat alias for the old ``HandleState`` namespace. New code
# should import :class:`SyncHandleState` from ``exlab_wizard.constants``.
HandleState = SyncHandleState

__all__ = [
    "BACKOFF_SCHEDULE_SECONDS",
    "MAX_ATTEMPTS",
    "HandleState",
    "NASSyncClient",
    "SyncHandleState",
    "SyncJobHandle",
    "SyncJobRow",
    "SyncJobState",
    "SyncQueue",
    "TransportError",
    "TransportErrorKind",
    "TransportResult",
    "Verifier",
    "VerifyResult",
    "cleanup_interlocks_satisfied",
    "effective_bandwidth_limit_kibps",
    "is_eligible",
]
