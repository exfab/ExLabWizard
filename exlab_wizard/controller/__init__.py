"""Controller package. Backend Spec §4.4.1, §4.7, §4.7.1, §4.8.

Re-exports the public surface of the creation controller -- the state
machine enums + transition helpers, the in-memory session store, and the
:class:`CreationController` class itself -- so callers can write
``from exlab_wizard.controller import CreationController, SessionState``
without reaching into the submodules.
"""

from exlab_wizard.controller.creation import (
    CreationController,
    NASSyncProtocol,
    NoOpNASSync,
    NoOpReadmeGenerator,
    ProjectCreateRequest,
    ReadmeContext,
    ReadmeGeneratorProtocol,
    RunCreateRequest,
    SessionHandle,
)
from exlab_wizard.controller.session_store import Session, SessionStore
from exlab_wizard.controller.state_machine import (
    VALID_TRANSITIONS,
    Phase,
    SessionState,
    assert_transition,
    state_to_phase,
)

__all__ = [
    "VALID_TRANSITIONS",
    "CreationController",
    "NASSyncProtocol",
    "NoOpNASSync",
    "NoOpReadmeGenerator",
    "Phase",
    "ProjectCreateRequest",
    "ReadmeContext",
    "ReadmeGeneratorProtocol",
    "RunCreateRequest",
    "Session",
    "SessionHandle",
    "SessionState",
    "SessionStore",
    "assert_transition",
    "state_to_phase",
]
