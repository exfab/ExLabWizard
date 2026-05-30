"""Startup update-check runner. Design Spec §15.6 / §15.8 item 3.

Mirrors the daemon-thread shape of :class:`exlab_wizard.tray.status.StatusTicker`
(idempotent ``start()``, ``stop()`` with a bounded join) but runs the check
exactly **once** at launch rather than on a cadence -- a fresh release rarely
appears mid-session, and a single startup probe keeps the network footprint to
one request. On finding a newer tag it fires one OS notification via
:func:`exlab_wizard.tray.notifications.notify`; the matching "Check for
updates…" tray item lets the operator re-check on demand.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from typing import Any

from exlab_wizard import __version__
from exlab_wizard.logging import get_logger
from exlab_wizard.tray.notifications import notify
from exlab_wizard.update_check.checker import fetch_latest_tag
from exlab_wizard.update_check.version import is_newer

__all__ = ["UpdateChecker"]

_log = get_logger(__name__)

# A fetcher returns ``(tag, url)`` or ``None`` (mirrors ``fetch_latest_tag``).
_Fetcher = Callable[[], Awaitable[tuple[str, str] | None]]


class UpdateChecker:
    """Runs the startup update probe on a daemon thread.

    ``fetcher`` defaults to :func:`fetch_latest_tag` and is injectable so
    tests drive the runner without any network. ``notifier`` is threaded
    through to :func:`notify`; tests pass a recording callable.
    """

    def __init__(
        self,
        *,
        current_version: str = __version__,
        notifier: Callable[..., Any] | None = None,
        fetcher: _Fetcher | None = None,
    ) -> None:
        self.current_version = current_version
        self._notifier = notifier
        self._fetch: _Fetcher = fetcher or fetch_latest_tag
        self._thread: threading.Thread | None = None
        # Populated once the probe succeeds; useful for a later auto-install
        # stage and for tests asserting what the probe discovered.
        self.latest_tag: str | None = None
        self.latest_url: str | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Spawn the one-shot check thread. Idempotent."""
        if self._thread is not None and self._thread.is_alive():
            return
        thread = threading.Thread(target=self._run, name="exlab-update-check", daemon=True)
        thread.start()
        self._thread = thread

    def stop(self) -> None:
        """Best-effort join of the check thread. Idempotent."""
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _run(self) -> None:
        """Thread body: run the async check once, never let an exception escape."""
        try:
            asyncio.run(self._check())
        except Exception:
            _log.exception("update_check.thread_failed")

    async def _check(self) -> None:
        """Fetch the latest tag and notify when it is newer than ``current``."""
        result = await self._fetch()
        if result is None:
            return
        tag, url = result
        self.latest_tag = tag
        self.latest_url = url
        if is_newer(self.current_version, tag):
            _log.info("update_check.update_available", extra={"latest_tag": tag})
            notify(
                title="ExLab-Wizard update available",
                message=f"Version {tag} is available — open the tray menu to download.",
                notifier=self._notifier,
            )
