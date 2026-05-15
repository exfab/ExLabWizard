"""Folder-feed client abstraction. GUI/Orchestrator Redesign §5.

A small ``start(path, on_update) / stop()`` surface that owns the
~2-3 s ``GET /folder/{path}`` poll for the centre-pane file list.
Returning to the same path is idempotent; switching paths stops the
previous poll before starting the new one. Paused on window background
/ foreground per §5.

The HTTP call is delegated through a caller-provided async function so
the module remains framework-agnostic (NiceGUI / pytest both reuse it).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

# Default poll cadence; the spec bounds it to ~2-3s.
FOLDER_FEED_POLL_INTERVAL_S: float = 2.5


@dataclass
class FolderFeedState:
    """In-memory state of one folder feed (one per centre pane)."""

    path: str | None = None
    paused: bool = False
    last_payload: Any | None = None


class FolderFeed:
    """One folder feed; rebound to a different path via ``start()``."""

    def __init__(
        self,
        *,
        fetch: Callable[[str], Awaitable[Any]],
        on_update: Callable[[Any], None] | None = None,
        poll_interval_s: float = FOLDER_FEED_POLL_INTERVAL_S,
    ) -> None:
        self._fetch = fetch
        self._on_update = on_update
        self._poll_interval_s = poll_interval_s
        self._state = FolderFeedState()
        self._task: asyncio.Task[None] | None = None

    @property
    def state(self) -> FolderFeedState:
        return self._state

    async def start(self, path: str) -> None:
        """Start polling ``path``. Switching paths stops the previous poll
        before starting the new one (the spec's `stop()` then `start()`
        lifecycle, called from the single ``on_select_node`` handler)."""
        if self._state.path == path and self._task is not None and not self._task.done():
            return
        await self.stop()
        self._state.path = path
        self._state.paused = False
        self._task = asyncio.create_task(self._loop(), name=f"exlab-folder-feed:{path}")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            import contextlib as _ctx

            with _ctx.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None
        self._state.path = None

    def pause(self) -> None:
        """Pause polling — used when the window is backgrounded."""
        self._state.paused = True

    def resume(self) -> None:
        """Resume polling after a pause."""
        self._state.paused = False

    async def _loop(self) -> None:
        assert self._state.path is not None
        path = self._state.path
        while True:
            try:
                if not self._state.paused and self._state.path == path:
                    payload = await self._fetch(path)
                    self._state.last_payload = payload
                    if self._on_update is not None:
                        self._on_update(payload)
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover -- transient network
                # Per §10: keep last good state on transient failure.
                pass
            await asyncio.sleep(self._poll_interval_s)
