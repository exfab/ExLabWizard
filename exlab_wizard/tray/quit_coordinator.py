"""Graceful tray shutdown coordinator. Backend Spec §4.3.2.

Steps documented in §4.3.2 (canonical):

1. Send the FastAPI lifespan shutdown signal; the server stops accepting
   new requests and ``POST /api/v1/sessions`` returns 503 with
   ``error.code: "shutting_down"``.
2. Wait up to **30 seconds** (5 seconds for SIGTERM at logoff, since the
   OS will hard-kill the process anyway) for the predicate
   ``SessionStore.active_sessions == 0 AND
   NASSyncClient.in_flight_jobs == 0``.
3. If the predicate becomes true within the window, exit cleanly.
4. If the timeout expires, prompt the operator with "1 operation still
   running. Force quit anyway?" via the open window if alive, otherwise
   via an OS notification. Operator picks **Force quit** (immediate
   shutdown; durable NAS-sync queue resumes on next launch) or **Wait**
   (resets the 30-second timer).

The coordinator is async because the predicate poll uses
``asyncio.sleep`` and the shutdown handoff to ``ServerRunner.stop``
naturally fits an async cleanup point. Tests can pass a 0-second timeout
to exercise the timeout branch deterministically.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from exlab_wizard.logging import get_logger

if TYPE_CHECKING:
    from exlab_wizard.tray.server_runner import ServerRunner
    from exlab_wizard.tray.window_launcher import WindowLauncher

__all__ = ["QuitCoordinator"]

_log = get_logger(__name__)


# Backend §4.3.2: 30-second normal timeout, 5-second SIGTERM timeout.
# Exposed as module-level constants so tests can monkeypatch.
DEFAULT_TIMEOUT_SECONDS: float = 30.0
SIGTERM_TIMEOUT_SECONDS: float = 5.0
PREDICATE_POLL_INTERVAL_SECONDS: float = 0.1


class QuitCoordinator:
    """Drive the §4.3.2 graceful-shutdown protocol.

    Construction-time dependencies are kept loosely typed so the
    coordinator can integrate with whatever stub fixtures unit tests
    pass. The runtime contract is documented per parameter.
    """

    def __init__(
        self,
        *,
        server_runner: ServerRunner,
        window_launcher: WindowLauncher | None,
        session_store: Any,
        nas_sync: Any,
        on_force_quit_prompt: Callable[[], bool] | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        sigterm_timeout_seconds: float = SIGTERM_TIMEOUT_SECONDS,
        poll_interval_seconds: float = PREDICATE_POLL_INTERVAL_SECONDS,
    ) -> None:
        self._server_runner = server_runner
        self._window_launcher = window_launcher
        self._session_store = session_store
        self._nas_sync = nas_sync
        self._on_force_quit_prompt = on_force_quit_prompt or (lambda: True)
        self._timeout = float(timeout_seconds)
        self._sigterm_timeout = float(sigterm_timeout_seconds)
        self._poll = float(poll_interval_seconds)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def quit(self, *, sigterm: bool = False) -> None:
        """Run the graceful-shutdown protocol.

        ``sigterm=True`` reduces the wait window to ``sigterm_timeout``
        (5 s by default) because the OS will hard-kill the process
        shortly anyway.
        """
        timeout = self._sigterm_timeout if sigterm else self._timeout
        _log.info("graceful shutdown initiated (sigterm=%s, timeout=%.1f)", sigterm, timeout)

        idle = await self._wait_for_idle(timeout)
        if not idle:
            _log.warning("graceful shutdown timed out; prompting for force-quit")
            if not self._prompt_force_quit():
                _log.info("operator chose Wait; resetting timer")
                # Fresh wait window; one extra retry per §4.3.2.
                idle = await self._wait_for_idle(timeout)
                if not idle:
                    _log.warning("still not idle after Wait; force-quitting anyway")

        # Tear down the live components in reverse-spawn order.
        if self._window_launcher is not None:
            self._window_launcher.close()
        self._server_runner.stop()
        _log.info("shutdown complete")

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _is_idle(self) -> bool:
        """Return True iff the §4.3.2 predicate holds.

        ``SessionStore.active_sessions == 0 AND
        NASSyncClient.in_flight_jobs == 0``. Both attributes are read
        defensively because the production wiring may pass loosely typed
        stubs (e.g. during setup-incomplete state where the session
        store is None).
        """
        active = _safe_count(self._session_store, "active_sessions")
        in_flight = _safe_count(self._nas_sync, "in_flight_jobs")
        return active == 0 and in_flight == 0

    async def _wait_for_idle(self, deadline_seconds: float) -> bool:
        """Poll :meth:`_is_idle` up to ``deadline_seconds`` seconds.

        Returns True if the predicate became true within the window.
        Sleeps in fixed intervals (``self._poll``) so a short deadline
        still produces a deterministic number of poll iterations in
        tests. Named ``deadline_seconds`` (not ``timeout``) to make
        clear that we are polling, not setting an asyncio cancellation
        timeout.
        """
        if self._is_idle():
            return True
        if deadline_seconds <= 0:
            return False
        deadline = asyncio.get_event_loop().time() + deadline_seconds
        while True:
            await asyncio.sleep(self._poll)
            if self._is_idle():
                return True
            if asyncio.get_event_loop().time() >= deadline:
                return False

    def _prompt_force_quit(self) -> bool:
        """Invoke the operator-facing force-quit prompt.

        Returns ``True`` to indicate "force quit anyway", ``False`` for
        "wait". Backend §4.3.2: prompt routed through the open window if
        alive, otherwise via OS notification. The launcher passes the
        actual UI-bound prompt at construction time; the coordinator is
        agnostic to which path it takes.
        """
        try:
            return bool(self._on_force_quit_prompt())
        except Exception:
            _log.exception("force-quit prompt raised; defaulting to force-quit")
            return True


def _safe_count(obj: Any, attr: str) -> int:
    """Return ``int(obj.<attr>)`` defensively.

    Falls through ``None``, missing attributes, callable attributes
    (some implementations expose the count via a method), and
    non-numeric returns -- the worst-case behavior is a count of 0,
    which is the "idle" interpretation. The launcher logs a warning
    once at startup if the wiring is not as expected; the coordinator
    itself does not gate shutdown on diagnostics.
    """
    if obj is None:
        return 0
    raw = getattr(obj, attr, 0)
    if callable(raw):
        try:
            raw = raw()
        except Exception:
            return 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0
